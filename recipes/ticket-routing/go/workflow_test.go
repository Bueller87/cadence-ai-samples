package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
	"go.uber.org/cadence"
	"go.uber.org/cadence/testsuite"
)

func TestTicketIntakeWorkflowRoutesEveryDepartment(t *testing.T) {
	tests := []struct {
		name       string
		department Department
		employeeID string
	}{
		{name: "billing", department: DepartmentBilling, employeeID: "SW-BILLING-101"},
		{name: "technical", department: DepartmentTechnical, employeeID: "SW-TECH-202"},
		{name: "account", department: DepartmentAccount, employeeID: "SW-ACCOUNT-303"},
		{name: "content", department: DepartmentContent, employeeID: "SW-CONTENT-404"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			env := newWorkflowEnvironment(t)
			ticket := Ticket{TicketID: "route-" + test.name, Message: "synthetic test ticket"}
			decision := validDecision(test.department)
			env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()

			env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

			require.True(t, env.IsWorkflowCompleted())
			require.NoError(t, env.GetWorkflowError())
			var result TicketResult
			require.NoError(t, env.GetWorkflowResult(&result))
			require.Equal(t, test.department, result.Department)
			require.Equal(t, test.employeeID, result.EmployeeID)
			require.Equal(t, StatusAssigned, result.Status)
			require.Equal(t, childWorkflowID(ticket.TicketID, test.department), result.ChildWorkflowID)
			env.AssertExpectations(t)
		})
	}
}

func TestDepartmentChildWorkflows(t *testing.T) {
	tests := []struct {
		name       string
		workflow   interface{}
		department Department
		employeeID string
	}{
		{name: "billing", workflow: BillingWorkflow, department: DepartmentBilling, employeeID: "SW-BILLING-101"},
		{name: "technical", workflow: TechnicalWorkflow, department: DepartmentTechnical, employeeID: "SW-TECH-202"},
		{name: "account", workflow: AccountWorkflow, department: DepartmentAccount, employeeID: "SW-ACCOUNT-303"},
		{name: "content", workflow: ContentWorkflow, department: DepartmentContent, employeeID: "SW-CONTENT-404"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var suite testsuite.WorkflowTestSuite
			env := suite.NewTestWorkflowEnvironment()
			input := ClassifiedTicket{
				Ticket:         Ticket{TicketID: "child-" + test.name, Message: "synthetic test ticket"},
				Classification: validDecision(test.department),
			}

			env.ExecuteWorkflow(test.workflow, input)

			require.True(t, env.IsWorkflowCompleted())
			require.NoError(t, env.GetWorkflowError())
			var result AssignmentResult
			require.NoError(t, env.GetWorkflowResult(&result))
			require.Equal(t, input.Ticket.TicketID, result.TicketID)
			require.Equal(t, test.department, result.Department)
			require.Equal(t, test.employeeID, result.EmployeeID)
			require.Equal(t, StatusAssigned, result.Status)
		})
	}
}

func TestTicketIntakeWorkflowReturnsUnroutableForUnsupportedClassification(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{TicketID: "invalid-001", Message: "synthetic test ticket"}
	decision := validDecision(Department("sales"))
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.True(t, env.IsWorkflowCompleted())
	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusUnroutable, result.Status)
	require.Contains(t, result.Reason, "unsupported department")
	require.Empty(t, result.ChildWorkflowID)
	require.Empty(t, result.EmployeeID)
	env.AssertExpectations(t)
}

func TestTicketIntakeWorkflowReturnsUnroutableForLowDepartmentConfidence(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{TicketID: "uncertain-001", Message: "synthetic uncertain ticket"}
	decision := validDecision(DepartmentBilling)
	decision.DepartmentConfidence = 0.64
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.True(t, env.IsWorkflowCompleted())
	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusUnroutable, result.Status)
	require.Contains(t, result.Reason, "below 0.65")
	require.Empty(t, result.ChildWorkflowID)
	env.AssertExpectations(t)
}

func TestMockClassifierProducesTypedDecision(t *testing.T) {
	decision, err := ClassifyTicket(t.Context(), Ticket{
		TicketID: "mock-001",
		Message:  "Urgent: I was charged twice for my subscription.",
	})

	require.NoError(t, err)
	require.Equal(t, DepartmentBilling, decision.Department)
	require.Equal(t, PriorityHigh, decision.Priority)
	require.Equal(t, ComplexityTier2, decision.Complexity)
	require.Equal(t, "mock-jev-v1", decision.Model)
}

func TestJevClassifierSendsAndParsesDocumentedContract(t *testing.T) {
	require.Equal(t, "https://api.typesafe.ai/v1/systemone", jevEndpoint)

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "/v1/systemone", request.URL.Path)
		require.Equal(t, http.MethodPost, request.Method)
		require.Equal(t, "Bearer test-api-key", request.Header.Get("Authorization"))
		require.Equal(t, "application/json", request.Header.Get("Content-Type"))

		var payload jevRequest
		require.NoError(t, json.NewDecoder(request.Body).Decode(&payload))
		require.Equal(t, "A synthetic billing request.", payload.State)
		require.Equal(t, jevModel, payload.Model)
		require.Len(t, payload.Questions, 3)
		requireChoiceQuestion(t, payload.Questions["department"], []string{"billing", "technical", "account", "content"})
		requireChoiceQuestion(t, payload.Questions["priority"], []string{"low", "normal", "high", "critical"})
		requireChoiceQuestion(t, payload.Questions["complexity"], []string{"tier1", "tier2", "tier3"})

		writer.Header().Set("Content-Type", "application/json")
		_, err := io.WriteString(writer, validJevResponse)
		require.NoError(t, err)
	}))
	defer server.Close()

	classifier, err := newJevClassifier("test-api-key", server.URL+"/v1/systemone", jevModel, server.Client())
	require.NoError(t, err)
	decision, err := classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "jev-001", Message: "A synthetic billing request."})

	require.NoError(t, err)
	require.Equal(t, DepartmentBilling, decision.Department)
	require.Equal(t, PriorityHigh, decision.Priority)
	require.Equal(t, ComplexityTier2, decision.Complexity)
	require.InDelta(t, 0.81, decision.DepartmentConfidence, 0.0001)
	require.InDelta(t, 0.72, decision.PriorityConfidence, 0.0001)
	require.InDelta(t, 0.68, decision.ComplexityConfidence, 0.0001)
	require.InDelta(t, 0.88, decision.DepartmentProbabilities["billing"], 0.0001)
	require.Equal(t, "jev-1.13.0", decision.Model)
	require.Equal(t, 412, decision.InputTokens)
	require.Equal(t, 47, decision.OutputTokens)
	require.False(t, decision.PriorityUncertain)
	require.False(t, decision.ComplexityUncertain)
}

func TestJevClassifierRequiresAPIKey(t *testing.T) {
	_, err := newJevClassifier("", jevEndpoint, jevModel, nil)
	require.Error(t, err)
	require.Contains(t, err.Error(), "TYPESAFE_API_KEY")

	t.Setenv("AI_PROVIDER", "jev")
	t.Setenv("TYPESAFE_API_KEY", "")
	_, _, err = configuredClassifier("worker")
	require.Error(t, err)
	require.Contains(t, err.Error(), "TYPESAFE_API_KEY")
}

func TestLiveDemoRequiresExplicitJevOptIn(t *testing.T) {
	t.Setenv("AI_PROVIDER", "")
	_, _, err := configuredClassifier("live-demo")
	require.Error(t, err)
	require.Contains(t, err.Error(), "AI_PROVIDER=jev")
}

func TestFourTicketDemoRejectsJevProvider(t *testing.T) {
	t.Setenv("AI_PROVIDER", "jev")
	_, _, err := configuredClassifier("demo")
	require.Error(t, err)
	require.Contains(t, err.Error(), "mock-only")
}

func TestJevClassifierMarksInformationalLowConfidenceAsUncertain(t *testing.T) {
	response := validJevResponseWithConfidence(0.40, 0.30)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		_, err := io.WriteString(writer, response)
		require.NoError(t, err)
	}))
	defer server.Close()

	classifier, err := newJevClassifier("test-api-key", server.URL, jevModel, server.Client())
	require.NoError(t, err)
	decision, err := classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "uncertain-002", Message: "Synthetic request."})
	require.NoError(t, err)
	require.True(t, decision.PriorityUncertain)
	require.True(t, decision.ComplexityUncertain)
	require.Equal(t, PriorityHigh, decision.Priority)
	require.Equal(t, ComplexityTier2, decision.Complexity)
}

func TestJevClassifierHTTPErrorRetryClassification(t *testing.T) {
	tests := []struct {
		name           string
		status         int
		nonRetryReason string
	}{
		{name: "rate limited", status: http.StatusTooManyRequests},
		{name: "provider overloaded", status: 529},
		{name: "server error", status: http.StatusInternalServerError},
		{name: "authentication", status: http.StatusUnauthorized, nonRetryReason: errReasonJevAuthentication},
		{name: "request validation", status: http.StatusUnprocessableEntity, nonRetryReason: errReasonJevRequestValidation},
		{name: "other client error", status: http.StatusBadRequest, nonRetryReason: errReasonJevClientRequest},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				writer.WriteHeader(test.status)
			}))
			defer server.Close()

			classifier, err := newJevClassifier("test-api-key", server.URL, jevModel, server.Client())
			require.NoError(t, err)
			_, err = classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "error-001", Message: "Synthetic request."})
			require.Error(t, err)

			var customError *cadence.CustomError
			if test.nonRetryReason == "" {
				require.False(t, errors.As(err, &customError), "transient errors must remain retryable")
			} else {
				require.ErrorAs(t, err, &customError)
				require.Equal(t, test.nonRetryReason, customError.Reason())
			}
		})
	}
}

func TestJevClassifierTreatsNetworkFailureAsRetryable(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	endpoint := server.URL
	server.Close()

	classifier, err := newJevClassifier("test-api-key", endpoint, jevModel, &http.Client{Timeout: 100 * time.Millisecond})
	require.NoError(t, err)
	_, err = classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "network-001", Message: "Synthetic request."})
	require.Error(t, err)

	var customError *cadence.CustomError
	require.False(t, errors.As(err, &customError), "network errors must remain retryable")
}

func TestJevClassifierRejectsMalformedSuccessfulResponses(t *testing.T) {
	tests := []struct {
		name string
		body string
	}{
		{name: "invalid JSON", body: "{"},
		{name: "missing model", body: `{"answers":{}}`},
		{name: "missing answer", body: `{"model":"jev-1.13.0","answers":{"department":{"type":"choice","choice":"billing","probabilities":{"billing":1},"confidence":1}}}`},
		{name: "missing probabilities", body: `{"model":"jev-1.13.0","answers":{"department":{"type":"choice","choice":"billing","confidence":1},"priority":{"type":"choice","choice":"normal","probabilities":{"normal":1},"confidence":1},"complexity":{"type":"choice","choice":"tier1","probabilities":{"tier1":1},"confidence":1}}}`},
		{name: "trailing data", body: validJevResponse + `{}`},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				writer.Header().Set("Content-Type", "application/json")
				_, err := io.WriteString(writer, test.body)
				require.NoError(t, err)
			}))
			defer server.Close()

			classifier, err := newJevClassifier("test-api-key", server.URL, jevModel, server.Client())
			require.NoError(t, err)
			_, err = classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "malformed-001", Message: "Synthetic request."})
			require.Error(t, err)
			var customError *cadence.CustomError
			require.ErrorAs(t, err, &customError)
			require.Equal(t, errReasonJevMalformedResponse, customError.Reason())
		})
	}
}

func requireChoiceQuestion(t *testing.T, question jevChoiceQuestion, expectedCriteria []string) {
	t.Helper()
	require.Equal(t, "choice", question.Type)
	require.NotEmpty(t, question.Instructions)
	require.Len(t, question.Criteria, len(expectedCriteria))
	for _, criterion := range expectedCriteria {
		require.NotEmpty(t, question.Criteria[criterion])
	}
}

const validJevResponse = `{
  "model": "jev-1.13.0",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": {"billing": 0.88, "technical": 0.06, "account": 0.04, "content": 0.02},
      "confidence": 0.81
    },
    "priority": {
      "type": "choice",
      "choice": "high",
      "probabilities": {"low": 0.01, "normal": 0.14, "high": 0.80, "critical": 0.05},
      "confidence": 0.72
    },
    "complexity": {
      "type": "choice",
      "choice": "tier2",
      "probabilities": {"tier1": 0.20, "tier2": 0.75, "tier3": 0.05},
      "confidence": 0.68
    }
  },
  "usage": {"input_tokens": 412, "output_tokens": 47}
}`

func validJevResponseWithConfidence(priorityConfidence, complexityConfidence float64) string {
	var response jevResponse
	if err := json.Unmarshal([]byte(validJevResponse), &response); err != nil {
		panic(err)
	}
	response.Answers["priority"] = withConfidence(response.Answers["priority"], priorityConfidence)
	response.Answers["complexity"] = withConfidence(response.Answers["complexity"], complexityConfidence)
	body, err := json.Marshal(response)
	if err != nil {
		panic(err)
	}
	return string(body)
}

func withConfidence(answer jevChoiceAnswer, confidence float64) jevChoiceAnswer {
	answer.Confidence = &confidence
	return answer
}

func newWorkflowEnvironment(t *testing.T) *testsuite.TestWorkflowEnvironment {
	t.Helper()
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	env.RegisterWorkflow(TicketIntakeWorkflow)
	env.RegisterWorkflow(BillingWorkflow)
	env.RegisterWorkflow(TechnicalWorkflow)
	env.RegisterWorkflow(AccountWorkflow)
	env.RegisterWorkflow(ContentWorkflow)
	env.RegisterActivity(ClassifyTicket)
	return env
}

func validDecision(department Department) RoutingDecision {
	return RoutingDecision{
		Department:           department,
		Priority:             PriorityNormal,
		Complexity:           ComplexityTier1,
		DepartmentConfidence: 0.95,
		PriorityConfidence:   0.90,
		ComplexityConfidence: 0.85,
		Model:                "mock-jev-v1",
	}
}
