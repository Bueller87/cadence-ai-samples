package main

import (
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
	"go.uber.org/cadence/.gen/go/shared"
	"go.uber.org/cadence/client"
	cadencemocks "go.uber.org/cadence/mocks"
	"go.uber.org/cadence/testsuite"
)

func TestWorkflowRecordsMockClassification(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := RecipeInput{Text: "Routine synthetic request."}
	expected := Classification{Label: "routine", Confidence: 0.9, Model: "mock-systemone-v1"}
	env.RegisterActivity(ClassifyText)
	env.OnActivity(ClassifyText, mock.Anything, input).Return(expected, nil).Once()

	env.ExecuteWorkflow(__RECIPE_CLASS__Workflow, input)

	require.NoError(t, env.GetWorkflowError())
	var result RecipeResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, input.Text, result.Input)
	require.Equal(t, expected, result.Classification)
	env.AssertExpectations(t)
}

func TestMockClassifierIsOfflineAndTyped(t *testing.T) {
	routine, err := ClassifyText(t.Context(), RecipeInput{Text: "Routine request."})
	require.NoError(t, err)
	require.Equal(t, "routine", routine.Label)

	priority, err := ClassifyText(t.Context(), RecipeInput{Text: "Urgent security outage."})
	require.NoError(t, err)
	require.Equal(t, "priority", priority.Label)
	require.Equal(t, "mock-systemone-v1", priority.Model)

	_, err = ClassifyText(t.Context(), RecipeInput{Text: "  "})
	require.Error(t, err)
}

func TestSystemOneClassifierSendsAndParsesChoiceContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "Bearer test-key", request.Header.Get("Authorization"))
		require.Equal(t, "application/json", request.Header.Get("Content-Type"))
		var payload systemOneRequest
		require.NoError(t, json.NewDecoder(request.Body).Decode(&payload))
		require.Equal(t, "Synthetic blocked request.", payload.State)
		require.Equal(t, "jev-latest", payload.Model)
		require.Contains(t, payload.Questions["route"].Criteria, "routine")
		require.Contains(t, payload.Questions["route"].Criteria, "priority")
		writer.Header().Set("Content-Type", "application/json")
		_, err := io.WriteString(writer, `{
			"model":"jev-test",
			"answers":{"route":{"type":"choice","choice":"priority",
			"probabilities":{"routine":0.1,"priority":0.9},"confidence":0.88}}
		}`)
		require.NoError(t, err)
	}))
	defer server.Close()

	classifier, err := newSystemOneClassifier("test-key", server.URL, "jev-latest", server.Client())
	require.NoError(t, err)
	result, err := classifier.ClassifyText(t.Context(), RecipeInput{Text: "Synthetic blocked request."})
	require.NoError(t, err)
	require.Equal(t, "priority", result.Label)
	require.Equal(t, "jev-test", result.Model)
	require.InDelta(t, 0.88, result.Confidence, 0.0001)
}

func TestSystemOneClassifierRejectsMalformedProbabilities(t *testing.T) {
	confidence := 0.8
	for name, probabilities := range map[string]map[string]float64{
		"negative":         {"routine": -0.1, "priority": 1.1},
		"not normalized":   {"routine": 0.2, "priority": 0.2},
		"missing label":    {"routine": 1},
		"unexpected label": {"routine": 0.5, "priority": 0.4, "other": 0.1},
		"not finite":       {"routine": math.NaN(), "priority": 0},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := classificationFromResponse(systemOneResponse{
				Model: "jev-test",
				Answers: map[string]systemOneChoiceAnswer{
					"route": {
						Type: "choice", Choice: "routine",
						Confidence: &confidence, Probabilities: probabilities,
					},
				},
			})
			require.Error(t, err)
		})
	}
}

func TestCatalogSelectionAndProviderValidation(t *testing.T) {
	directory := writeCatalog(t, `classifiers:
  - id: jev-default
    provider: typesafe
    model: jev-latest
    endpoint: https://api.typesafe.ai/v1/systemone
  - id: laya-local
    provider: laya
    model: convaiinnovations/laya
    endpoint: http://localhost:8008/v1/systemone
`)
	jev, err := loadClassifier(directory, "jev-default")
	require.NoError(t, err)
	require.Equal(t, classifierProviderTypeSafe, jev.Provider)
	_, err = loadClassifier(directory, "missing")
	require.ErrorContains(t, err, "unknown classifier ID")

	for _, config := range []ClassifierConfig{
		{ID: "remote-laya", Provider: "laya", Model: "m", Endpoint: "https://example.com/v1/systemone"},
		{ID: "insecure-jev", Provider: "typesafe", Model: "m", Endpoint: "http://api.typesafe.ai/v1/systemone"},
		{ID: "credentials", Provider: "laya", Model: "m", Endpoint: "http://user:pass@localhost:8008/v1/systemone"},
		{ID: "candidate", Provider: "other", Model: "m", Endpoint: "https://example.com"},
	} {
		require.Error(t, validateClassifier(config))
	}
}

func TestConfiguredClassifierValidatesKeysWithoutCallingProvider(t *testing.T) {
	directory := writeCatalog(t, `classifiers:
  - id: jev-default
    provider: typesafe
    model: jev-latest
    endpoint: https://api.typesafe.ai/v1/systemone
  - id: laya-local
    provider: laya
    model: convaiinnovations/laya
    endpoint: http://localhost:8008/v1/systemone
`)
	t.Setenv("CLASSIFIER_AI_KEY", "")
	_, _, err := configuredClassifier("worker", "jev-default", directory)
	require.ErrorContains(t, err, "CLASSIFIER_AI_KEY")

	selected, activity, err := configuredClassifier("worker", "laya-local", directory)
	require.NoError(t, err)
	require.Equal(t, "laya-local", selected)
	require.NotNil(t, activity)

	selected, activity, err = configuredClassifier("live-demo", "jev-default", directory)
	require.NoError(t, err)
	require.Equal(t, "jev-default", selected)
	require.Nil(t, activity, "starters must not construct or call provider clients")
}

func TestChangedCatalogEndpointReachesClassifierTransport(t *testing.T) {
	called := false
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		called = true
		require.Equal(t, "/changed-systemone", request.URL.Path)
		writer.Header().Set("Content-Type", "application/json")
		_, err := io.WriteString(writer, `{
			"model":"changed-model",
			"answers":{"route":{"type":"choice","choice":"routine",
			"probabilities":{"routine":0.8,"priority":0.2},"confidence":0.8}}
		}`)
		require.NoError(t, err)
	}))
	defer server.Close()

	directory := writeCatalog(t, "classifiers:\n"+
		"  - id: laya-local\n"+
		"    provider: laya\n"+
		"    model: changed-model\n"+
		"    endpoint: "+server.URL+"/changed-systemone\n")
	_, activity, err := configuredClassifier("worker", "laya-local", directory)
	require.NoError(t, err)
	classify, ok := activity.(func(context.Context, RecipeInput) (Classification, error))
	require.True(t, ok)
	result, err := classify(t.Context(), RecipeInput{Text: "Synthetic request."})
	require.NoError(t, err)
	require.True(t, called)
	require.Equal(t, "changed-model", result.Model)
}

func TestModesRequireMatchingClassifierAndLiveConfirmation(t *testing.T) {
	directory := writeCatalog(t, `classifiers:
  - id: laya-local
    provider: laya
    model: convaiinnovations/laya
    endpoint: http://localhost:8008/v1/systemone
`)
	_, _, err := configuredClassifier("live-demo", mockClassifierID, directory)
	require.ErrorContains(t, err, "explicit live")
	_, _, err = configuredClassifier("demo", "laya-local", directory)
	require.ErrorContains(t, err, "mock-only")
	require.ErrorContains(t, validateMode("worker", "laya-local", false), "-confirm-live")
	require.ErrorContains(t, validateMode("live-demo", "laya-local", false), "-confirm-live")
	require.NoError(t, validateMode("worker", "laya-local", true))
	require.NoError(t, validateMode("demo", mockClassifierID, false))
	require.Error(t, validateMode("unknown", mockClassifierID, false))
}

func TestStartOptionsAllowClosedRunReuseAndRejectOpenDuplicates(t *testing.T) {
	options := startOptions("custom-task-list", "__RECIPE_SLUG__-demo")
	require.Equal(t, "__RECIPE_SLUG__-demo", options.ID)
	require.Equal(t, "custom-task-list", options.TaskList)
	require.Equal(t, client.WorkflowIDReusePolicyAllowDuplicate, options.WorkflowIDReusePolicy)
	// Cadence's ALLOW_DUPLICATE policy permits a new Run only after the previous
	// Run closes. The service still rejects a duplicate while a Run is open.
	require.NotEqual(t, client.WorkflowIDReusePolicyTerminateIfRunning, options.WorkflowIDReusePolicy)
}

func TestStartReportsDuplicateOpenRun(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	cadenceClient.On(
		"ExecuteWorkflow", mock.Anything, mock.Anything, mock.Anything, mock.Anything,
	).Return(nil, &shared.WorkflowExecutionAlreadyStartedError{}).Once()

	err := start(
		context.Background(), cadenceClient, "test-task-list",
		"__RECIPE_SLUG__-demo", "Synthetic request.",
	)

	require.ErrorContains(t, err, "already has an open Run")
	require.ErrorContains(t, err, "different -workflow-id")
}

func writeCatalog(t *testing.T, contents string) string {
	t.Helper()
	directory := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(directory, "classifiers.yaml"), []byte(contents), 0o600))
	return directory
}
