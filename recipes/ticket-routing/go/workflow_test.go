package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
	"go.uber.org/cadence"
	"go.uber.org/cadence/client"
	"go.uber.org/cadence/encoded"
	cadencemocks "go.uber.org/cadence/mocks"
	"go.uber.org/cadence/testsuite"
	"go.uber.org/cadence/workflow"
)

func TestSyntheticTicketDataset(t *testing.T) {
	file, err := os.Open(filepath.Join("..", "testdata", "tickets.jsonl"))
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, file.Close()) })

	validDepartments := map[string]bool{"billing": true, "technical": true, "account": true, "content": true}
	validPriorities := map[string]bool{"low": true, "normal": true, "high": true, "critical": true}
	validComplexities := map[string]bool{"tier1": true, "tier2": true, "tier3": true}
	seenIDs := make(map[string]bool)
	departmentCounts := make(map[string]int)
	recordCount := 0

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		recordCount++
		var record struct {
			TicketID           string `json:"ticket_id"`
			Message            string `json:"message"`
			ExpectedDepartment string `json:"expected_department"`
			ExpectedPriority   string `json:"expected_priority"`
			ExpectedComplexity string `json:"expected_complexity"`
		}
		require.NoErrorf(t, json.Unmarshal(scanner.Bytes(), &record), "invalid JSON on line %d", recordCount)
		require.NotEmptyf(t, record.TicketID, "missing ticket_id on line %d", recordCount)
		require.NotEmptyf(t, record.Message, "missing message on line %d", recordCount)
		require.Falsef(t, seenIDs[record.TicketID], "duplicate ticket_id %q", record.TicketID)
		require.Truef(t, validDepartments[record.ExpectedDepartment], "invalid department %q on line %d", record.ExpectedDepartment, recordCount)
		require.Truef(t, validPriorities[record.ExpectedPriority], "invalid priority %q on line %d", record.ExpectedPriority, recordCount)
		require.Truef(t, validComplexities[record.ExpectedComplexity], "invalid complexity %q on line %d", record.ExpectedComplexity, recordCount)

		seenIDs[record.TicketID] = true
		departmentCounts[record.ExpectedDepartment]++
	}

	require.NoError(t, scanner.Err())
	require.Equal(t, 40, recordCount)
	for department := range validDepartments {
		require.Equalf(t, 10, departmentCounts[department], "unexpected %s record count", department)
	}
}

func TestStartManualTicketDisplaysAcknowledgmentDetails(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	run := cadencemocks.NewWorkflowRun(t)
	run.On("GetRunID").Return("run-manual-001").Once()
	run.On("Get", mock.Anything, mock.Anything).Run(func(arguments mock.Arguments) {
		result := arguments.Get(1).(*TicketResult)
		*result = TicketResult{
			TicketID:   "manual-test-001",
			Department: DepartmentBilling,
			EmployeeID: BillingEmployeeID,
			Status:     StatusAcknowledged,
			SLAMet:     true,
		}
	}).Return(nil).Once()
	cadenceClient.On(
		"ExecuteWorkflow",
		mock.Anything,
		mock.MatchedBy(func(options client.StartWorkflowOptions) bool {
			return options.ID == "ticket-routing-manual-test-001" &&
				options.TaskList == TaskList &&
				options.ExecutionStartToCloseTimeout == 7*time.Minute
		}),
		mock.Anything,
		mock.MatchedBy(func(ticket Ticket) bool {
			return ticket.TicketID == "manual-test-001" &&
				ticket.Message == manualDemoTicketMessage &&
				ticket.SLA.High == 2*time.Minute
		}),
	).Return(run, nil).Once()

	var output bytes.Buffer
	err := startManualTicket(context.Background(), cadenceClient, TaskList, "manual-test-001", 2*time.Minute, &output)

	require.NoError(t, err)
	require.Contains(t, output.String(), "parent workflow ID: ticket-routing-manual-test-001")
	require.Contains(t, output.String(), "parent run ID: run-manual-001")
	require.Contains(t, output.String(), "child workflow ID: ticket-routing-manual-test-001-child-billing")
	require.Contains(t, output.String(), "employee ID: "+BillingEmployeeID)
	require.Contains(t, output.String(), "assignment ID: manual-test-001:billing:"+BillingEmployeeID)
	require.Contains(t, output.String(), "signal name: "+AcknowledgmentSignalName)
	require.Contains(t, output.String(), "go run . -mode acknowledge")
	require.Contains(t, output.String(), "Workflow status: Completed")
	require.Contains(t, output.String(), "Ticket ID: manual-test-001")
	require.Contains(t, output.String(), "Department: billing")
	require.Contains(t, output.String(), "Assigned employee: "+BillingEmployeeID)
	require.Contains(t, output.String(), "Business status: ACKNOWLEDGED")
	require.Contains(t, output.String(), "SLA met: YES")
}

func TestAcknowledgeManualTicketSendsTypedSignal(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	expected := AcknowledgmentSignal{
		TicketID:     "manual-test-002",
		EmployeeID:   BillingEmployeeID,
		AssignmentID: "manual-test-002:billing:" + BillingEmployeeID,
	}
	cadenceClient.On(
		"SignalWorkflow",
		mock.Anything,
		"ticket-routing-manual-test-002-child-billing",
		"",
		AcknowledgmentSignalName,
		expected,
	).Return(nil).Once()

	var output bytes.Buffer
	err := acknowledgeManualTicket(context.Background(), cadenceClient, expected.TicketID, DepartmentBilling, expected.EmployeeID, "", &output)

	require.NoError(t, err)
	require.Contains(t, output.String(), "acknowledgment sent")
	require.Contains(t, output.String(), expected.AssignmentID)
}

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
			ticket := Ticket{TicketID: "route-" + test.name, Message: "synthetic test ticket", SLA: testSLAConfig(10 * time.Second)}
			decision := validDecision(test.department)
			env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()
			var childID string
			env.SetOnChildWorkflowStartedListener(func(info *workflow.Info, _ workflow.Context, _ encoded.Values) {
				childID = info.WorkflowExecution.ID
			})
			acknowledgment := AcknowledgmentSignal{
				TicketID:     ticket.TicketID,
				EmployeeID:   test.employeeID,
				AssignmentID: assignmentIdentifier(ticket.TicketID, test.department, test.employeeID),
			}
			env.RegisterDelayedCallback(func() {
				require.NoError(t, env.SignalWorkflowByID(childID, AcknowledgmentSignalName, acknowledgment))
			}, time.Second)

			env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

			require.True(t, env.IsWorkflowCompleted())
			require.NoError(t, env.GetWorkflowError())
			var result TicketResult
			require.NoError(t, env.GetWorkflowResult(&result))
			require.Equal(t, test.department, result.Department)
			require.Equal(t, test.employeeID, result.EmployeeID)
			require.Equal(t, acknowledgment.AssignmentID, result.AssignmentID)
			require.Equal(t, StatusAcknowledged, result.Status)
			require.True(t, result.SLAMet)
			require.NotEmpty(t, childID)
			require.Contains(t, childID, "-child-"+string(test.department))
			require.Equal(t, childID, result.ChildWorkflowID)
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
				Ticket:         Ticket{TicketID: "child-" + test.name, Message: "synthetic test ticket", SLA: testSLAConfig(10 * time.Second)},
				Classification: validDecision(test.department),
			}
			acknowledgment := AcknowledgmentSignal{
				TicketID:     input.Ticket.TicketID,
				EmployeeID:   test.employeeID,
				AssignmentID: assignmentIdentifier(input.Ticket.TicketID, test.department, test.employeeID),
			}
			env.RegisterDelayedCallback(func() {
				env.SignalWorkflow(AcknowledgmentSignalName, acknowledgment)
			}, time.Second)

			env.ExecuteWorkflow(test.workflow, input)

			require.True(t, env.IsWorkflowCompleted())
			require.NoError(t, env.GetWorkflowError())
			var result AssignmentResult
			require.NoError(t, env.GetWorkflowResult(&result))
			require.Equal(t, input.Ticket.TicketID, result.TicketID)
			require.Equal(t, test.department, result.Department)
			require.Equal(t, test.employeeID, result.EmployeeID)
			require.Equal(t, acknowledgment.AssignmentID, result.AssignmentID)
			require.Equal(t, StatusAcknowledged, result.Status)
			require.True(t, result.SLAMet)
			require.Equal(t, acknowledgment, *result.Acknowledgment)
		})
	}
}

func TestDepartmentChildWorkflowTimesOutWithoutAcknowledgment(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := classifiedTicketForTest("timeout-001", DepartmentBilling, time.Second)

	env.ExecuteWorkflow(BillingWorkflow, input)

	require.True(t, env.IsWorkflowCompleted())
	require.NoError(t, env.GetWorkflowError())
	var result AssignmentResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusSLATimeout, result.Status)
	require.False(t, result.SLAMet)
	require.Equal(t, time.Second, result.SLADuration)
	require.Nil(t, result.Acknowledgment)
}

func TestDepartmentChildWorkflowIgnoresInvalidAcknowledgments(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := classifiedTicketForTest("invalid-ack-001", DepartmentBilling, 10*time.Second)
	valid := acknowledgmentForTest(input, "SW-BILLING-101")

	env.RegisterDelayedCallback(func() {
		invalid := valid
		invalid.TicketID = "another-ticket"
		env.SignalWorkflow(AcknowledgmentSignalName, invalid)
	}, time.Second)
	env.RegisterDelayedCallback(func() {
		invalid := valid
		invalid.EmployeeID = "SW-BILLING-999"
		env.SignalWorkflow(AcknowledgmentSignalName, invalid)
	}, 2*time.Second)
	env.RegisterDelayedCallback(func() {
		invalid := valid
		invalid.AssignmentID = "stale-assignment"
		env.SignalWorkflow(AcknowledgmentSignalName, invalid)
	}, 3*time.Second)
	env.RegisterDelayedCallback(func() {
		env.SignalWorkflow(AcknowledgmentSignalName, valid)
	}, 4*time.Second)

	env.ExecuteWorkflow(BillingWorkflow, input)

	require.NoError(t, env.GetWorkflowError())
	var result AssignmentResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusAcknowledged, result.Status)
	require.True(t, result.SLAMet)
	require.Equal(t, valid, *result.Acknowledgment)
}

func TestDepartmentChildWorkflowIgnoresDuplicateAcknowledgment(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := classifiedTicketForTest("duplicate-001", DepartmentTechnical, 10*time.Second)
	acknowledgment := acknowledgmentForTest(input, "SW-TECH-202")
	env.RegisterDelayedCallback(func() {
		env.SignalWorkflowSkippingDecision(AcknowledgmentSignalName, acknowledgment)
		env.SignalWorkflow(AcknowledgmentSignalName, acknowledgment)
	}, time.Second)

	env.ExecuteWorkflow(TechnicalWorkflow, input)

	require.NoError(t, env.GetWorkflowError())
	var result AssignmentResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusAcknowledged, result.Status)
	require.True(t, result.SLAMet)
	require.Equal(t, acknowledgment, *result.Acknowledgment)
}

func TestDepartmentChildWorkflowRejectsLateAcknowledgment(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := classifiedTicketForTest("late-001", DepartmentAccount, time.Second)
	acknowledgment := acknowledgmentForTest(input, "SW-ACCOUNT-303")
	env.RegisterDelayedCallback(func() {
		env.SignalWorkflow(AcknowledgmentSignalName, acknowledgment)
	}, 2*time.Second)

	env.ExecuteWorkflow(AccountWorkflow, input)

	require.NoError(t, env.GetWorkflowError())
	var result AssignmentResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusSLATimeout, result.Status)
	require.False(t, result.SLAMet)
	require.Nil(t, result.Acknowledgment)
}

func TestDepartmentChildWorkflowTimeoutWinsSignalDeadlineRace(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := classifiedTicketForTest("race-001", DepartmentContent, time.Second)
	acknowledgment := acknowledgmentForTest(input, "SW-CONTENT-404")
	env.RegisterDelayedCallback(func() {
		env.SignalWorkflow(AcknowledgmentSignalName, acknowledgment)
	}, time.Second)

	env.ExecuteWorkflow(ContentWorkflow, input)

	require.NoError(t, env.GetWorkflowError())
	var result AssignmentResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusSLATimeout, result.Status)
	require.False(t, result.SLAMet)
	require.Nil(t, result.Acknowledgment)
}

func TestCustomWorkflowControlAcknowledgesAndCancelsTimer(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := classifiedTicketForTest("cwc-001", DepartmentBilling, 10*time.Second)
	acknowledgment := acknowledgmentForTest(input, BillingEmployeeID)
	timerCancelled := false
	env.SetOnTimerCancelledListener(func(string) {
		timerCancelled = true
	})

	env.RegisterDelayedCallback(func() {
		control := queryAssignmentControl(t, env)
		require.Equal(t, "formattedData", control.CadenceResponseType)
		require.Equal(t, "text/markdown", control.Format)
		require.Contains(t, control.Data, "Ticket ID:** `cwc-001`")
		require.Contains(t, control.Data, "Department:** `billing`")
		require.Contains(t, control.Data, "Assigned employee:** `"+BillingEmployeeID+"`")
		require.Contains(t, control.Data, "Priority:** `normal`")
		require.Contains(t, control.Data, "SLA duration:** `10s`")
		require.Contains(t, control.Data, "Current acknowledgment status:** `AWAITING_ACKNOWLEDGMENT`")
		require.Contains(t, control.Data, `signalName="`+AcknowledgmentSignalName+`"`)
		require.Contains(t, control.Data, `label="Acknowledge Ticket"`)
		require.Contains(t, control.Data, `input={"ticket_id":"cwc-001","employee_id":"`+BillingEmployeeID+`","assignment_id":"cwc-001:billing:`+BillingEmployeeID+`"}`)

		// This is the same typed payload sent by the CWC button.
		env.SignalWorkflow(AcknowledgmentSignalName, acknowledgment)
	}, time.Second)

	env.ExecuteWorkflow(BillingWorkflow, input)

	require.NoError(t, env.GetWorkflowError())
	var result AssignmentResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusAcknowledged, result.Status)
	require.True(t, result.SLAMet)
	require.True(t, timerCancelled)

	terminalControl := queryAssignmentControl(t, env)
	require.NotContains(t, terminalControl.Data, "{% signal")
	require.Contains(t, terminalControl.Data, "Current acknowledgment status:** `ACKNOWLEDGED`")
	require.Contains(t, terminalControl.Data, "SLA met:** YES")
}

func TestCustomWorkflowControlHasNoActionAfterTimeout(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	input := classifiedTicketForTest("cwc-timeout-001", DepartmentContent, time.Second)

	env.ExecuteWorkflow(ContentWorkflow, input)

	require.NoError(t, env.GetWorkflowError())
	var result AssignmentResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusSLATimeout, result.Status)
	require.False(t, result.SLAMet)

	terminalControl := queryAssignmentControl(t, env)
	require.NotContains(t, terminalControl.Data, "{% signal")
	require.Contains(t, terminalControl.Data, "Current acknowledgment status:** `SLA_TIMEOUT`")
	require.Contains(t, terminalControl.Data, "SLA met:** NO")
}

func TestAcknowledgmentSLADurationsByPriority(t *testing.T) {
	config := SLAConfig{
		Critical: 2 * time.Second,
		High:     4 * time.Second,
		Normal:   6 * time.Second,
		Low:      8 * time.Second,
	}
	require.Equal(t, 2*time.Second, acknowledgmentSLA(PriorityCritical, config))
	require.Equal(t, 4*time.Second, acknowledgmentSLA(PriorityHigh, config))
	require.Equal(t, 6*time.Second, acknowledgmentSLA(PriorityNormal, config))
	require.Equal(t, 8*time.Second, acknowledgmentSLA(PriorityLow, config))
}

func TestWorkflowExecutionTimeoutsIncludeSLAAndSchedulingOverhead(t *testing.T) {
	sla := 90 * time.Second
	config := testSLAConfig(sla)

	require.Equal(t, 3*time.Minute+30*time.Second, childWorkflowExecutionTimeout(sla))
	require.Equal(t, 6*time.Minute+30*time.Second, parentWorkflowExecutionTimeout(config))
	require.Greater(t, childWorkflowExecutionTimeout(sla), sla)
	require.Greater(t, parentWorkflowExecutionTimeout(config), classificationScheduleToClose+childWorkflowExecutionTimeout(sla))
}

func TestTicketIntakeWorkflowAllowsSLAOverOneMinute(t *testing.T) {
	env := newWorkflowEnvironment(t)
	env.OnActivity(ClassifyTicket, mock.Anything, mock.Anything).Return(validDecision(DepartmentBilling), nil).Once()
	var childExecutionTimeout time.Duration
	env.SetOnChildWorkflowStartedListener(func(info *workflow.Info, _ workflow.Context, _ encoded.Values) {
		childExecutionTimeout = time.Duration(info.ExecutionStartToCloseTimeoutSeconds) * time.Second
	})

	env.ExecuteWorkflow(TicketIntakeWorkflow, Ticket{
		TicketID: "delayed-child-001",
		Message:  "I was charged twice.",
		SLA:      testSLAConfig(90 * time.Second),
	})

	require.True(t, env.IsWorkflowCompleted())
	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusSLATimeout, result.Status)
	require.Equal(t, 90*time.Second, result.SLADuration)
	require.Equal(t, 3*time.Minute+30*time.Second, childExecutionTimeout)
}

func TestTicketIntakeWorkflowReturnsSLATimeout(t *testing.T) {
	env := newWorkflowEnvironment(t)
	env.OnActivity(ClassifyTicket, mock.Anything, mock.Anything).Return(validDecision(DepartmentBilling), nil).Once()

	env.ExecuteWorkflow(TicketIntakeWorkflow, Ticket{
		TicketID: "parent-timeout-001",
		Message:  "I was charged twice.",
		SLA:      testSLAConfig(time.Second),
	})

	require.True(t, env.IsWorkflowCompleted())
	require.NoError(t, env.GetWorkflowError())

	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusSLATimeout, result.Status)
	require.False(t, result.SLAMet)
	require.Equal(t, DepartmentBilling, result.Department)
	require.Equal(t, "SW-BILLING-101", result.EmployeeID)
	require.Equal(t, assignmentIdentifier("parent-timeout-001", DepartmentBilling, "SW-BILLING-101"), result.AssignmentID)
	require.Equal(t, time.Second, result.SLADuration)
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

func TestManualTicketStarterRejectsJevProvider(t *testing.T) {
	t.Setenv("AI_PROVIDER", "jev")
	_, _, err := configuredClassifier("start-ticket")
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

func queryAssignmentControl(t *testing.T, env *testsuite.TestWorkflowEnvironment) MarkdownFormattedResponse {
	t.Helper()
	value, err := env.QueryWorkflow(AssignmentQueryName)
	require.NoError(t, err)
	var response MarkdownFormattedResponse
	require.NoError(t, value.Get(&response))
	return response
}

func classifiedTicketForTest(ticketID string, department Department, sla time.Duration) ClassifiedTicket {
	return ClassifiedTicket{
		Ticket: Ticket{
			TicketID: ticketID,
			Message:  "synthetic test ticket",
			SLA:      testSLAConfig(sla),
		},
		Classification: validDecision(department),
	}
}

func acknowledgmentForTest(ticket ClassifiedTicket, employeeID string) AcknowledgmentSignal {
	return AcknowledgmentSignal{
		TicketID:     ticket.Ticket.TicketID,
		EmployeeID:   employeeID,
		AssignmentID: assignmentIdentifier(ticket.Ticket.TicketID, ticket.Classification.Department, employeeID),
	}
}

func testSLAConfig(duration time.Duration) SLAConfig {
	return SLAConfig{
		Critical: duration,
		High:     duration,
		Normal:   duration,
		Low:      duration,
	}
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
