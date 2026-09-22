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
	TaskList = "ticket-routing"

	AcknowledgmentSignalName = "acknowledge-assignment"
	AssignmentQueryName      = "ticket-assignment"
	BillingEmployeeID        = "SW-BILLING-101"
	TechnicalEmployeeID      = "SW-TECH-202"
	AccountEmployeeID        = "SW-ACCOUNT-303"
	ContentEmployeeID        = "SW-CONTENT-404"

	StatusAwaitingAcknowledgment = "AWAITING_ACKNOWLEDGMENT"
	StatusAcknowledged           = "ACKNOWLEDGED"
	StatusSLATimeout             = "SLA_TIMEOUT"
	StatusUnroutable             = "UNROUTABLE"

	minimumDepartmentConfidence      = 0.65
	minimumInformationalConfidence   = 0.65
	classificationScheduleToClose    = 2 * time.Minute
	childWorkflowSchedulingOverhead  = 2 * time.Minute
	parentWorkflowSchedulingOverhead = time.Minute

	jevEndpoint = "https://api.typesafe.ai/v1/systemone"
	jevModel    = "jev-latest"

	errReasonJevConfiguration     = "JevConfigurationError"
	errReasonJevAuthentication    = "JevAuthenticationError"
	errReasonJevRequestValidation = "JevRequestValidationError"
	errReasonJevClientRequest     = "JevClientRequestError"
	errReasonJevMalformedResponse = "JevMalformedResponseError"
)

type Department string

const (
	DepartmentBilling   Department = "billing"
	DepartmentTechnical Department = "technical"
	DepartmentAccount   Department = "account"
	DepartmentContent   Department = "content"
)

type Priority string

const (
	PriorityLow      Priority = "low"
	PriorityNormal   Priority = "normal"
	PriorityHigh     Priority = "high"
	PriorityCritical Priority = "critical"
)

type Complexity string

const (
	ComplexityTier1 Complexity = "tier1"
	ComplexityTier2 Complexity = "tier2"
	ComplexityTier3 Complexity = "tier3"
)

// Ticket is the input to one TicketIntakeWorkflow execution.
type Ticket struct {
	TicketID string    `json:"ticket_id"`
	Message  string    `json:"message"`
	SLA      SLAConfig `json:"acknowledgment_sla,omitempty"`
}

// SLAConfig contains demonstration acknowledgment deadlines by priority.
// Zero values use the documented defaults.
type SLAConfig struct {
	Critical time.Duration `json:"critical,omitempty"`
	High     time.Duration `json:"high,omitempty"`
	Normal   time.Duration `json:"normal,omitempty"`
	Low      time.Duration `json:"low,omitempty"`
}

// RoutingDecision is the typed result returned by the classification Activity.
type RoutingDecision struct {
	Department              Department         `json:"department"`
	Priority                Priority           `json:"priority"`
	Complexity              Complexity         `json:"complexity"`
	DepartmentConfidence    float64            `json:"department_confidence"`
	PriorityConfidence      float64            `json:"priority_confidence"`
	ComplexityConfidence    float64            `json:"complexity_confidence"`
	DepartmentProbabilities map[string]float64 `json:"department_probabilities,omitempty"`
	PriorityProbabilities   map[string]float64 `json:"priority_probabilities,omitempty"`
	ComplexityProbabilities map[string]float64 `json:"complexity_probabilities,omitempty"`
	PriorityUncertain       bool               `json:"priority_uncertain"`
	ComplexityUncertain     bool               `json:"complexity_uncertain"`
	Model                   string             `json:"model"`
	InputTokens             int                `json:"input_tokens,omitempty"`
	OutputTokens            int                `json:"output_tokens,omitempty"`
	TokenUsageReported      bool               `json:"token_usage_reported"`
	InferenceLatency        time.Duration      `json:"inference_latency,omitempty"`
}

// ClassifiedTicket is passed from the parent to a department Child Workflow.
type ClassifiedTicket struct {
	Ticket         Ticket          `json:"ticket"`
	Classification RoutingDecision `json:"classification"`
}

// AssignmentResult is returned by a department Child Workflow.
type AssignmentResult struct {
	TicketID       string                `json:"ticket_id"`
	Department     Department            `json:"department"`
	EmployeeID     string                `json:"employee_id"`
	AssignmentID   string                `json:"assignment_id"`
	SLADuration    time.Duration         `json:"sla_duration"`
	SLADeadline    time.Time             `json:"sla_deadline"`
	SLAMet         bool                  `json:"sla_met"`
	Status         string                `json:"status"`
	Acknowledgment *AcknowledgmentSignal `json:"acknowledgment,omitempty"`
}

// AcknowledgmentSignal identifies the current ticket assignment. All fields
// must match before a department Child Workflow can complete successfully.
type AcknowledgmentSignal struct {
	TicketID     string `json:"ticket_id"`
	EmployeeID   string `json:"employee_id"`
	AssignmentID string `json:"assignment_id"`
}

// MarkdownFormattedResponse is the response envelope Cadence Web expects for
// Markdown-based Custom Workflow Controls.
type MarkdownFormattedResponse struct {
	CadenceResponseType string `json:"cadenceResponseType"`
	Format              string `json:"format"`
	Data                string `json:"data"`
}

// TicketResult is the completed result returned by the parent Workflow.
type TicketResult struct {
	TicketID        string          `json:"ticket_id"`
	Classification  RoutingDecision `json:"classification"`
	Department      Department      `json:"department,omitempty"`
	EmployeeID      string          `json:"employee_id,omitempty"`
	AssignmentID    string          `json:"assignment_id,omitempty"`
	ChildWorkflowID string          `json:"child_workflow_id,omitempty"`
	SLADuration     time.Duration   `json:"sla_duration,omitempty"`
	SLADeadline     time.Time       `json:"sla_deadline,omitempty"`
	SLAMet          bool            `json:"sla_met"`
	Status          string          `json:"status"`
	Reason          string          `json:"reason,omitempty"`
}

// TicketIntakeWorkflow classifies one ticket and routes it to one department.
func TicketIntakeWorkflow(ctx workflow.Context, ticket Ticket) (TicketResult, error) {
	activityOptions := workflow.ActivityOptions{
		ScheduleToStartTimeout: time.Minute,
		StartToCloseTimeout:    30 * time.Second,
		ScheduleToCloseTimeout: classificationScheduleToClose,
		RetryPolicy: &cadence.RetryPolicy{
			InitialInterval:    time.Second,
			BackoffCoefficient: 2,
			MaximumInterval:    10 * time.Second,
			ExpirationInterval: 2 * time.Minute,
			NonRetriableErrorReasons: []string{
				errReasonJevConfiguration,
				errReasonJevAuthentication,
				errReasonJevRequestValidation,
				errReasonJevClientRequest,
				errReasonJevMalformedResponse,
			},
		},
	}
	ctx = workflow.WithActivityOptions(ctx, activityOptions)

	var decision RoutingDecision
	if err := workflow.ExecuteActivity(ctx, ClassifyTicket, ticket).Get(ctx, &decision); err != nil {
		return TicketResult{}, fmt.Errorf("classify ticket: %w", err)
	}

	if reason := invalidClassificationReason(decision); reason != "" {
		return TicketResult{
			TicketID:       ticket.TicketID,
			Classification: decision,
			Status:         StatusUnroutable,
			Reason:         reason,
		}, nil
	}

	classified := ClassifiedTicket{Ticket: ticket, Classification: decision}
	parentWorkflowID := workflow.GetInfo(ctx).WorkflowExecution.ID
	childID := childWorkflowID(parentWorkflowID, decision.Department)
	childContext := workflow.WithChildOptions(ctx, workflow.ChildWorkflowOptions{
		WorkflowID:                   childID,
		ExecutionStartToCloseTimeout: childWorkflowExecutionTimeout(acknowledgmentSLA(decision.Priority, ticket.SLA)),
		TaskStartToCloseTimeout:      10 * time.Second,
	})

	var childWorkflow interface{}
	switch decision.Department {
	case DepartmentBilling:
		childWorkflow = BillingWorkflow
	case DepartmentTechnical:
		childWorkflow = TechnicalWorkflow
	case DepartmentAccount:
		childWorkflow = AccountWorkflow
	case DepartmentContent:
		childWorkflow = ContentWorkflow
	default:
		return TicketResult{}, fmt.Errorf("unsupported department %q", decision.Department)
	}

	var assignment AssignmentResult
	if err := workflow.ExecuteChildWorkflow(childContext, childWorkflow, classified).Get(childContext, &assignment); err != nil {
		return TicketResult{}, fmt.Errorf("run %s child workflow: %w", decision.Department, err)
	}

	return TicketResult{
		TicketID:        ticket.TicketID,
		Classification:  decision,
		Department:      assignment.Department,
		EmployeeID:      assignment.EmployeeID,
		AssignmentID:    assignment.AssignmentID,
		ChildWorkflowID: childID,
		SLADuration:     assignment.SLADuration,
		SLADeadline:     assignment.SLADeadline,
		SLAMet:          assignment.SLAMet,
		Status:          assignment.Status,
	}, nil
}

// ClassifyTicket is the default deterministic mock Jev classifier.
func ClassifyTicket(_ context.Context, ticket Ticket) (RoutingDecision, error) {
	if strings.TrimSpace(ticket.TicketID) == "" {
		return RoutingDecision{}, fmt.Errorf("ticket ID is required")
	}
	if strings.TrimSpace(ticket.Message) == "" {
		return RoutingDecision{}, fmt.Errorf("ticket message is required")
	}

	message := strings.ToLower(ticket.Message)
	decision := RoutingDecision{
		Department:           classifyDepartment(message),
		Priority:             classifyPriority(message),
		Complexity:           classifyComplexity(message),
		DepartmentConfidence: 0.92,
		PriorityConfidence:   0.88,
		ComplexityConfidence: 0.84,
		Model:                "mock-jev-v1",
	}

	return decision, nil
}

// JevClassifier implements the same Activity contract as ClassifyTicket using
// TypeSafe's System One HTTP API. It is registered only when explicitly enabled.
type JevClassifier struct {
	apiKey     string
	endpoint   string
	model      string
	httpClient *http.Client
}

type jevRequest struct {
	State     string                       `json:"state"`
	Model     string                       `json:"model"`
	Questions map[string]jevChoiceQuestion `json:"questions"`
}

type jevChoiceQuestion struct {
	Type         string            `json:"type"`
	Instructions string            `json:"instructions"`
	Criteria     map[string]string `json:"criteria"`
}

type jevResponse struct {
	Model   string                     `json:"model"`
	Answers map[string]jevChoiceAnswer `json:"answers"`
	Usage   *jevUsage                  `json:"usage,omitempty"`
}

type jevChoiceAnswer struct {
	Type          string             `json:"type"`
	Choice        string             `json:"choice"`
	Probabilities map[string]float64 `json:"probabilities"`
	Confidence    *float64           `json:"confidence"`
}

type jevUsage struct {
	InputTokens  *int `json:"input_tokens"`
	OutputTokens *int `json:"output_tokens"`
}

func newJevClassifier(apiKey, endpoint, model string, httpClient *http.Client) (*JevClassifier, error) {
	if strings.TrimSpace(apiKey) == "" {
		return nil, fmt.Errorf("TYPESAFE_API_KEY is required when AI_PROVIDER=jev")
	}
	if strings.TrimSpace(endpoint) == "" {
		return nil, fmt.Errorf("Jev endpoint is required")
	}
	if strings.TrimSpace(model) == "" {
		return nil, fmt.Errorf("Jev model is required")
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 20 * time.Second}
	}

	return &JevClassifier{
		apiKey:     apiKey,
		endpoint:   endpoint,
		model:      model,
		httpClient: httpClient,
	}, nil
}

// ClassifyTicket sends one request containing three independent Choice questions.
func (classifier *JevClassifier) ClassifyTicket(ctx context.Context, ticket Ticket) (RoutingDecision, error) {
	if classifier == nil || strings.TrimSpace(classifier.apiKey) == "" {
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevConfiguration, "TYPESAFE_API_KEY is not configured")
	}
	if strings.TrimSpace(ticket.TicketID) == "" {
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevRequestValidation, "ticket ID is required")
	}
	if strings.TrimSpace(ticket.Message) == "" {
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevRequestValidation, "ticket message is required")
	}

	payload := jevRequest{
		State: ticket.Message,
		Model: classifier.model,
		Questions: map[string]jevChoiceQuestion{
			"department": {
				Type:         "choice",
				Instructions: "Which StreamWave support department is primarily responsible for this customer request?",
				Criteria: map[string]string{
					string(DepartmentBilling):   "Subscription charges, payments, invoices, refunds, or billing disputes.",
					string(DepartmentTechnical): "Playback failures, buffering, application crashes, device errors, or service outages.",
					string(DepartmentAccount):   "Sign-in, password, profile, email-address, access, or account-management issues.",
					string(DepartmentContent):   "Catalog availability, missing episodes, subtitles, captions, audio tracks, or content metadata.",
				},
			},
			"priority": {
				Type:         "choice",
				Instructions: "What is the operational urgency of this request based on impact and time sensitivity, not sentiment alone?",
				Criteria: map[string]string{
					string(PriorityLow):      "Minor question, suggestion, or inconvenience with no meaningful time pressure.",
					string(PriorityNormal):   "A standard support issue affecting normal use without severe or time-critical impact.",
					string(PriorityHigh):     "A major loss of functionality, repeated financial issue, or time-sensitive access problem.",
					string(PriorityCritical): "A widespread outage, credible account-security incident, severe repeated charging, or similarly urgent impact.",
				},
			},
			"complexity": {
				Type:         "choice",
				Instructions: "What level of support expertise is likely needed to investigate this request?",
				Criteria: map[string]string{
					string(ComplexityTier1): "Routine issue with a documented resolution or straightforward account/content correction.",
					string(ComplexityTier2): "Issue requiring deeper investigation, multiple checks, or product-specific troubleshooting.",
					string(ComplexityTier3): "Unusual, cross-system, security-sensitive, or technically complex issue requiring specialist investigation.",
				},
			},
		},
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevRequestValidation, "encode Jev request")
	}

	request, err := http.NewRequestWithContext(ctx, http.MethodPost, classifier.endpoint, bytes.NewReader(body))
	if err != nil {
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevConfiguration, "create Jev request")
	}
	request.Header.Set("Authorization", "Bearer "+classifier.apiKey)
	request.Header.Set("Content-Type", "application/json")

	inferenceStarted := time.Now()
	response, err := classifier.httpClient.Do(request)
	if err != nil {
		return RoutingDecision{}, fmt.Errorf("Jev request failed: %w", err)
	}
	defer response.Body.Close()

	switch {
	case response.StatusCode == http.StatusUnauthorized:
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevAuthentication, "Jev rejected the configured API key")
	case response.StatusCode == http.StatusUnprocessableEntity:
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevRequestValidation, "Jev rejected the request schema")
	case response.StatusCode == http.StatusTooManyRequests || response.StatusCode == 529 || response.StatusCode >= 500:
		return RoutingDecision{}, fmt.Errorf("Jev temporary HTTP failure: %d", response.StatusCode)
	case response.StatusCode < 200 || response.StatusCode >= 300:
		return RoutingDecision{}, cadence.NewCustomError(errReasonJevClientRequest, fmt.Sprintf("Jev returned HTTP %d", response.StatusCode))
	}

	var decoded jevResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, 1<<20))
	if err := decoder.Decode(&decoded); err != nil {
		return RoutingDecision{}, malformedJevResponse("decode response JSON")
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		return RoutingDecision{}, malformedJevResponse("response contains trailing data")
	}

	decision, err := routingDecisionFromJev(decoded)
	if err != nil {
		return RoutingDecision{}, err
	}
	decision.InferenceLatency = time.Since(inferenceStarted)
	return decision, nil
}

func routingDecisionFromJev(response jevResponse) (RoutingDecision, error) {
	if strings.TrimSpace(response.Model) == "" {
		return RoutingDecision{}, malformedJevResponse("missing model")
	}

	department, err := requiredChoiceAnswer(response.Answers, "department")
	if err != nil {
		return RoutingDecision{}, err
	}
	priority, err := requiredChoiceAnswer(response.Answers, "priority")
	if err != nil {
		return RoutingDecision{}, err
	}
	complexity, err := requiredChoiceAnswer(response.Answers, "complexity")
	if err != nil {
		return RoutingDecision{}, err
	}

	decision := RoutingDecision{
		Department:              Department(department.Choice),
		Priority:                Priority(priority.Choice),
		Complexity:              Complexity(complexity.Choice),
		DepartmentConfidence:    *department.Confidence,
		PriorityConfidence:      *priority.Confidence,
		ComplexityConfidence:    *complexity.Confidence,
		DepartmentProbabilities: department.Probabilities,
		PriorityProbabilities:   priority.Probabilities,
		ComplexityProbabilities: complexity.Probabilities,
		PriorityUncertain:       *priority.Confidence < minimumInformationalConfidence,
		ComplexityUncertain:     *complexity.Confidence < minimumInformationalConfidence,
		Model:                   response.Model,
	}

	if response.Usage != nil {
		if response.Usage.InputTokens != nil {
			if *response.Usage.InputTokens < 0 {
				return RoutingDecision{}, malformedJevResponse("negative input token usage")
			}
			decision.InputTokens = *response.Usage.InputTokens
		}
		if response.Usage.OutputTokens != nil {
			if *response.Usage.OutputTokens < 0 {
				return RoutingDecision{}, malformedJevResponse("negative output token usage")
			}
			decision.OutputTokens = *response.Usage.OutputTokens
		}
		decision.TokenUsageReported = response.Usage.InputTokens != nil && response.Usage.OutputTokens != nil
	}

	return decision, nil
}

func requiredChoiceAnswer(answers map[string]jevChoiceAnswer, name string) (jevChoiceAnswer, error) {
	answer, ok := answers[name]
	if !ok {
		return jevChoiceAnswer{}, malformedJevResponse("missing " + name + " answer")
	}
	if answer.Type != "choice" {
		return jevChoiceAnswer{}, malformedJevResponse(name + " answer is not a choice")
	}
	if strings.TrimSpace(answer.Choice) == "" {
		return jevChoiceAnswer{}, malformedJevResponse(name + " answer has no selected choice")
	}
	if answer.Confidence == nil || math.IsNaN(*answer.Confidence) || math.IsInf(*answer.Confidence, 0) || *answer.Confidence < 0 || *answer.Confidence > 1 {
		return jevChoiceAnswer{}, malformedJevResponse(name + " answer has invalid confidence")
	}
	if len(answer.Probabilities) == 0 {
		return jevChoiceAnswer{}, malformedJevResponse(name + " answer has no probabilities")
	}

	probabilitySum := 0.0
	for _, probability := range answer.Probabilities {
		if math.IsNaN(probability) || math.IsInf(probability, 0) || probability < 0 || probability > 1 {
			return jevChoiceAnswer{}, malformedJevResponse(name + " answer has an invalid probability")
		}
		probabilitySum += probability
	}
	if _, ok := answer.Probabilities[answer.Choice]; !ok {
		return jevChoiceAnswer{}, malformedJevResponse(name + " selected choice is absent from probabilities")
	}
	if math.Abs(probabilitySum-1) > 0.02 {
		return jevChoiceAnswer{}, malformedJevResponse(name + " probabilities do not sum to one")
	}

	return answer, nil
}

func malformedJevResponse(message string) error {
	return cadence.NewCustomError(errReasonJevMalformedResponse, message)
}

func classifyDepartment(message string) Department {
	switch {
	case containsAny(message, "bill", "charge", "payment", "refund", "invoice"):
		return DepartmentBilling
	case containsAny(message, "password", "sign in", "login", "email address", "profile", "account"):
		return DepartmentAccount
	case containsAny(message, "subtitle", "caption", "episode", "show", "movie", "audio", "catalog"):
		return DepartmentContent
	default:
		return DepartmentTechnical
	}
}

func classifyPriority(message string) Priority {
	switch {
	case containsAny(message, "security", "hacked", "charged many times", "service is down"):
		return PriorityCritical
	case containsAny(message, "urgent", "can't watch", "cannot watch", "locked out", "charged twice"):
		return PriorityHigh
	case containsAny(message, "when possible", "suggestion", "feature request"):
		return PriorityLow
	default:
		return PriorityNormal
	}
}

func classifyComplexity(message string) Complexity {
	switch {
	case containsAny(message, "every device", "all devices", "data loss", "charged many times"):
		return ComplexityTier3
	case containsAny(message, "multiple", "charged twice", "keeps crashing", "intermittent"):
		return ComplexityTier2
	default:
		return ComplexityTier1
	}
}

func containsAny(message string, values ...string) bool {
	for _, value := range values {
		if strings.Contains(message, value) {
			return true
		}
	}
	return false
}

func invalidClassificationReason(decision RoutingDecision) string {
	if !validDepartment(decision.Department) {
		return fmt.Sprintf("unsupported department %q", decision.Department)
	}
	if !validPriority(decision.Priority) {
		return fmt.Sprintf("unsupported priority %q", decision.Priority)
	}
	if !validComplexity(decision.Complexity) {
		return fmt.Sprintf("unsupported complexity %q", decision.Complexity)
	}
	if decision.DepartmentConfidence < minimumDepartmentConfidence {
		return fmt.Sprintf("department confidence %.2f is below %.2f", decision.DepartmentConfidence, minimumDepartmentConfidence)
	}
	return ""
}

func validDepartment(value Department) bool {
	return value == DepartmentBilling || value == DepartmentTechnical || value == DepartmentAccount || value == DepartmentContent
}

func validPriority(value Priority) bool {
	return value == PriorityLow || value == PriorityNormal || value == PriorityHigh || value == PriorityCritical
}

func validComplexity(value Complexity) bool {
	return value == ComplexityTier1 || value == ComplexityTier2 || value == ComplexityTier3
}

func childWorkflowID(parentWorkflowID string, department Department) string {
	return fmt.Sprintf("%s-child-%s", parentWorkflowID, department)
}

func BillingWorkflow(ctx workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ctx, ticket, DepartmentBilling, BillingEmployeeID)
}

func TechnicalWorkflow(ctx workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ctx, ticket, DepartmentTechnical, TechnicalEmployeeID)
}

func AccountWorkflow(ctx workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ctx, ticket, DepartmentAccount, AccountEmployeeID)
}

func ContentWorkflow(ctx workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ctx, ticket, DepartmentContent, ContentEmployeeID)
}

func assignTicket(ctx workflow.Context, ticket ClassifiedTicket, department Department, employeeID string) (AssignmentResult, error) {
	if ticket.Classification.Department != department {
		return AssignmentResult{}, fmt.Errorf("%s workflow received %s ticket", department, ticket.Classification.Department)
	}

	assignmentID := assignmentIdentifier(ticket.Ticket.TicketID, department, employeeID)
	slaDuration := acknowledgmentSLA(ticket.Classification.Priority, ticket.Ticket.SLA)
	slaDeadline := workflow.Now(ctx).Add(slaDuration)
	result := AssignmentResult{
		TicketID:     ticket.Ticket.TicketID,
		Department:   department,
		EmployeeID:   employeeID,
		AssignmentID: assignmentID,
		SLADuration:  slaDuration,
		SLADeadline:  slaDeadline,
		Status:       StatusAwaitingAcknowledgment,
	}
	if err := workflow.SetQueryHandler(ctx, AssignmentQueryName, func() (MarkdownFormattedResponse, error) {
		return assignmentControlResponse(ctx, ticket.Classification.Priority, result), nil
	}); err != nil {
		return AssignmentResult{}, fmt.Errorf("register assignment query: %w", err)
	}

	signalChannel := workflow.GetSignalChannel(ctx, AcknowledgmentSignalName)
	timerCtx, cancelTimer := workflow.WithCancel(ctx)
	timer := workflow.NewTimer(timerCtx, slaDuration)

	for {
		if timer.IsReady() {
			result.Status = StatusSLATimeout
			return result, nil
		}

		var acknowledgment AcknowledgmentSignal
		signalReceived := false
		selector := workflow.NewSelector(ctx)
		selector.AddReceive(signalChannel, func(channel workflow.Channel, _ bool) {
			channel.Receive(ctx, &acknowledgment)
			signalReceived = true
		})
		selector.AddFuture(timer, func(workflow.Future) {})
		selector.Select(ctx)

		// Timeout wins when both events are ready in the same workflow task. This
		// makes the signal/deadline boundary deterministic and rejects late signals.
		if timer.IsReady() {
			result.Status = StatusSLATimeout
			return result, nil
		}
		if signalReceived && validAcknowledgment(acknowledgment, result) {
			result.Status = StatusAcknowledged
			result.SLAMet = true
			result.Acknowledgment = &acknowledgment
			cancelTimer()
			_ = timer.Get(ctx, nil)
			return result, nil
		}
	}
}

func assignmentControlResponse(ctx workflow.Context, priority Priority, assignment AssignmentResult) MarkdownFormattedResponse {
	markdown := fmt.Sprintf("## Ticket assignment\n\n- **Ticket ID:** `%s`\n- **Department:** `%s`\n- **Assigned employee:** `%s`\n- **Priority:** `%s`\n- **SLA duration:** `%s`\n- **SLA deadline:** `%s`\n- **Current acknowledgment status:** `%s`\n",
		assignment.TicketID,
		assignment.Department,
		assignment.EmployeeID,
		priority,
		assignment.SLADuration,
		assignment.SLADeadline.UTC().Format(time.RFC3339),
		assignment.Status,
	)

	if assignment.Status == StatusAwaitingAcknowledgment {
		payload, err := json.Marshal(AcknowledgmentSignal{
			TicketID:     assignment.TicketID,
			EmployeeID:   assignment.EmployeeID,
			AssignmentID: assignment.AssignmentID,
		})
		if err == nil {
			info := workflow.GetInfo(ctx)
			markdown += fmt.Sprintf("\n{%% signal signalName=%q label=%q domain=%q cluster=%q workflowId=%q runId=%q input=%s /%%}\n",
				AcknowledgmentSignalName, "Acknowledge Ticket", info.Domain, "cluster0", info.WorkflowExecution.ID, info.WorkflowExecution.RunID, payload)
		}
	} else {
		markdown += fmt.Sprintf("\n- **SLA met:** %s\n\n*Assignment is complete; no actions are available.*\n", slaMetLabel(assignment.Status, assignment.SLAMet))
	}

	return MarkdownFormattedResponse{
		CadenceResponseType: "formattedData",
		Format:              "text/markdown",
		Data:                markdown,
	}
}

func slaMetLabel(status string, slaMet bool) string {
	switch {
	case status == StatusAcknowledged && slaMet:
		return "YES"
	case status == StatusSLATimeout && !slaMet:
		return "NO"
	default:
		return "N/A"
	}
}

func validAcknowledgment(acknowledgment AcknowledgmentSignal, assignment AssignmentResult) bool {
	return acknowledgment.TicketID == assignment.TicketID &&
		acknowledgment.EmployeeID == assignment.EmployeeID &&
		acknowledgment.AssignmentID == assignment.AssignmentID
}

func assignmentIdentifier(ticketID string, department Department, employeeID string) string {
	return fmt.Sprintf("%s:%s:%s", ticketID, department, employeeID)
}

func acknowledgmentSLA(priority Priority, config SLAConfig) time.Duration {
	defaults := defaultSLAConfig()
	switch priority {
	case PriorityCritical:
		if config.Critical > 0 {
			return config.Critical
		}
		return defaults.Critical
	case PriorityHigh:
		if config.High > 0 {
			return config.High
		}
		return defaults.High
	case PriorityNormal:
		if config.Normal > 0 {
			return config.Normal
		}
		return defaults.Normal
	case PriorityLow:
		if config.Low > 0 {
			return config.Low
		}
		return defaults.Low
	default:
		return defaults.Normal
	}
}

func childWorkflowExecutionTimeout(sla time.Duration) time.Duration {
	return sla + childWorkflowSchedulingOverhead
}

func parentWorkflowExecutionTimeout(config SLAConfig) time.Duration {
	longestSLA := acknowledgmentSLA(PriorityCritical, config)
	for _, priority := range []Priority{PriorityHigh, PriorityNormal, PriorityLow} {
		if sla := acknowledgmentSLA(priority, config); sla > longestSLA {
			longestSLA = sla
		}
	}
	return classificationScheduleToClose + childWorkflowExecutionTimeout(longestSLA) + parentWorkflowSchedulingOverhead
}

func defaultSLAConfig() SLAConfig {
	return SLAConfig{
		Critical: 5 * time.Second,
		High:     10 * time.Second,
		Normal:   20 * time.Second,
		Low:      30 * time.Second,
	}
}
