package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"strings"
	"time"

	"go.uber.org/cadence"
	"go.uber.org/cadence/workflow"
)

const (
	TaskList = "__RECIPE_SLUG__"

	classificationScheduleToClose = 2 * time.Minute
	errClassifierConfiguration    = "ClassifierConfigurationError"
	errClassifierAuthentication   = "ClassifierAuthenticationError"
	errClassifierRequest          = "ClassifierRequestError"
	errClassifierResponse         = "ClassifierResponseError"
)

// RecipeInput is the input to one __RECIPE_CLASS__Workflow execution.
type RecipeInput struct {
	Text string `json:"text"`
}

// Classification is the typed result recorded in Workflow history.
type Classification struct {
	Label         string             `json:"label"`
	Confidence    float64            `json:"confidence"`
	Probabilities map[string]float64 `json:"probabilities,omitempty"`
	Model         string             `json:"model"`
}

// RecipeResult is the completed Workflow result.
type RecipeResult struct {
	Input          string         `json:"input"`
	Classification Classification `json:"classification"`
}

// __RECIPE_CLASS__Workflow keeps the classifier call in an Activity.
func __RECIPE_CLASS__Workflow(ctx workflow.Context, input RecipeInput) (RecipeResult, error) {
	ctx = workflow.WithActivityOptions(ctx, workflow.ActivityOptions{
		ScheduleToStartTimeout: time.Minute,
		StartToCloseTimeout:    30 * time.Second,
		ScheduleToCloseTimeout: classificationScheduleToClose,
		RetryPolicy: &cadence.RetryPolicy{
			InitialInterval:    time.Second,
			BackoffCoefficient: 2,
			MaximumInterval:    10 * time.Second,
			ExpirationInterval: classificationScheduleToClose,
			NonRetriableErrorReasons: []string{
				errClassifierConfiguration,
				errClassifierAuthentication,
				errClassifierRequest,
				errClassifierResponse,
			},
		},
	})

	var classification Classification
	if err := workflow.ExecuteActivity(ctx, ClassifyText, input).Get(ctx, &classification); err != nil {
		return RecipeResult{}, fmt.Errorf("classify text: %w", err)
	}
	return RecipeResult{Input: input.Text, Classification: classification}, nil
}

// ClassifyText is the deterministic offline classifier used by default.
func ClassifyText(_ context.Context, input RecipeInput) (Classification, error) {
	text := strings.TrimSpace(input.Text)
	if text == "" {
		return Classification{}, cadence.NewCustomError(errClassifierRequest, "text is required")
	}

	label := "routine"
	confidence := 0.90
	if containsAny(strings.ToLower(text), "urgent", "blocked", "outage", "security") {
		label = "priority"
		confidence = 0.95
	}
	return Classification{
		Label:         label,
		Confidence:    confidence,
		Probabilities: map[string]float64{label: confidence},
		Model:         "mock-systemone-v1",
	}, nil
}

func containsAny(text string, values ...string) bool {
	for _, value := range values {
		if strings.Contains(text, value) {
			return true
		}
	}
	return false
}

// SystemOneClassifier implements the same Activity contract through HTTP.
type SystemOneClassifier struct {
	apiKey     string
	endpoint   string
	model      string
	httpClient *http.Client
}

type systemOneRequest struct {
	State     string                             `json:"state"`
	Model     string                             `json:"model"`
	Questions map[string]systemOneChoiceQuestion `json:"questions"`
}

type systemOneChoiceQuestion struct {
	Type         string            `json:"type"`
	Instructions string            `json:"instructions"`
	Criteria     map[string]string `json:"criteria"`
}

type systemOneResponse struct {
	Model   string                           `json:"model"`
	Answers map[string]systemOneChoiceAnswer `json:"answers"`
}

type systemOneChoiceAnswer struct {
	Type          string             `json:"type"`
	Choice        string             `json:"choice"`
	Probabilities map[string]float64 `json:"probabilities"`
	Confidence    *float64           `json:"confidence"`
}

func newSystemOneClassifier(apiKey, endpoint, model string, httpClient *http.Client) (*SystemOneClassifier, error) {
	if strings.TrimSpace(endpoint) == "" || strings.TrimSpace(model) == "" {
		return nil, fmt.Errorf("classifier endpoint and model are required")
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 20 * time.Second}
	}
	return &SystemOneClassifier{
		apiKey:     strings.TrimSpace(apiKey),
		endpoint:   endpoint,
		model:      model,
		httpClient: httpClient,
	}, nil
}

// ClassifyText performs all live network work inside the Activity.
func (classifier *SystemOneClassifier) ClassifyText(ctx context.Context, input RecipeInput) (Classification, error) {
	if classifier == nil {
		return Classification{}, cadence.NewCustomError(errClassifierConfiguration, "classifier is not configured")
	}
	if strings.TrimSpace(input.Text) == "" {
		return Classification{}, cadence.NewCustomError(errClassifierRequest, "text is required")
	}

	payload := systemOneRequest{
		State: input.Text,
		Model: classifier.model,
		Questions: map[string]systemOneChoiceQuestion{
			"route": {
				Type:         "choice",
				Instructions: "Should this request receive routine handling or priority review?",
				Criteria: map[string]string{
					"routine":  "Normal request without an urgent operational or security impact.",
					"priority": "Urgent, blocked, outage-related, or security-sensitive request.",
				},
			},
		},
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return Classification{}, cadence.NewCustomError(errClassifierRequest, "encode classifier request")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, classifier.endpoint, bytes.NewReader(body))
	if err != nil {
		return Classification{}, cadence.NewCustomError(errClassifierConfiguration, "create classifier request")
	}
	request.Header.Set("Content-Type", "application/json")
	if classifier.apiKey != "" {
		request.Header.Set("Authorization", "Bearer "+classifier.apiKey)
	}

	response, err := classifier.httpClient.Do(request)
	if err != nil {
		return Classification{}, fmt.Errorf("classifier request failed: %w", err)
	}
	defer response.Body.Close()
	switch {
	case response.StatusCode == http.StatusUnauthorized:
		return Classification{}, cadence.NewCustomError(errClassifierAuthentication, "classifier rejected the configured API key")
	case response.StatusCode == http.StatusTooManyRequests || response.StatusCode == 529 || response.StatusCode >= 500:
		return Classification{}, fmt.Errorf("classifier temporary HTTP failure: %d", response.StatusCode)
	case response.StatusCode < 200 || response.StatusCode >= 300:
		return Classification{}, cadence.NewCustomError(errClassifierRequest, fmt.Sprintf("classifier returned HTTP %d", response.StatusCode))
	}

	var decoded systemOneResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, 1<<20))
	if err := decoder.Decode(&decoded); err != nil {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "decode response JSON")
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "response contains trailing data")
	}
	return classificationFromResponse(decoded)
}

func classificationFromResponse(response systemOneResponse) (Classification, error) {
	answer, ok := response.Answers["route"]
	if strings.TrimSpace(response.Model) == "" || !ok || answer.Type != "choice" {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "missing model or route choice")
	}
	if answer.Choice != "routine" && answer.Choice != "priority" {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "unsupported route choice")
	}
	if answer.Confidence == nil || math.IsNaN(*answer.Confidence) || math.IsInf(*answer.Confidence, 0) ||
		*answer.Confidence < 0 || *answer.Confidence > 1 {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "invalid route confidence")
	}
	if len(answer.Probabilities) != 2 {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "route probabilities must contain routine and priority")
	}
	probabilitySum := 0.0
	for _, label := range []string{"routine", "priority"} {
		probability, ok := answer.Probabilities[label]
		if !ok || math.IsNaN(probability) || math.IsInf(probability, 0) ||
			probability < 0 || probability > 1 {
			return Classification{}, cadence.NewCustomError(errClassifierResponse, "invalid route probabilities")
		}
		probabilitySum += probability
	}
	if math.Abs(probabilitySum-1) > 0.02 {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "route probabilities do not sum to one")
	}
	if _, ok := answer.Probabilities[answer.Choice]; !ok {
		return Classification{}, cadence.NewCustomError(errClassifierResponse, "selected route is absent from probabilities")
	}
	return Classification{
		Label:         answer.Choice,
		Confidence:    *answer.Confidence,
		Probabilities: answer.Probabilities,
		Model:         response.Model,
	}, nil
}
