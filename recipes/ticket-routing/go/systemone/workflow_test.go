package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
	"time"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
	"go.uber.org/cadence"
	"go.uber.org/cadence/.gen/go/shared"
	"go.uber.org/cadence/activity"
	"go.uber.org/cadence/encoded"
	"go.uber.org/cadence/testsuite"
	"go.uber.org/cadence/worker"
	"go.uber.org/cadence/workflow"
)

func TestSyntheticTicketDataset(t *testing.T) {
	file, err := os.Open(filepath.Join("..", "..", "testdata", "tickets.jsonl"))
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

		decision, err := ClassifyTicket(t.Context(), Ticket{
			TicketID: record.TicketID,
			Message:  record.Message,
		})
		require.NoErrorf(t, err, "mock classification failed on line %d", recordCount)
		require.Truef(t, validDepartments[string(decision.Department)], "mock returned invalid department on line %d", recordCount)
		require.Truef(t, validPriorities[string(decision.Priority)], "mock returned invalid priority on line %d", recordCount)
		require.Truef(t, validComplexities[string(decision.Complexity)], "mock returned invalid complexity on line %d", recordCount)

		seenIDs[record.TicketID] = true
		departmentCounts[record.ExpectedDepartment]++
	}

	require.NoError(t, scanner.Err())
	require.Equal(t, 40, recordCount)
	for department := range validDepartments {
		require.Equalf(t, 10, departmentCounts[department], "unexpected %s record count", department)
	}
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

func TestParentAssignmentStatusQueryTransitions(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{
		TicketID: "query-001",
		Message:  "synthetic test ticket",
		SLA:      testSLAConfig(10 * time.Second),
	}
	decision := validDecision(DepartmentBilling)
	var pending ParentAssignmentStatus
	env.SetOnActivityStartedListener(func(_ *activity.Info, _ context.Context, _ encoded.Values) {
		pending = queryParentAssignmentStatus(t, env)
		control := queryAssignmentControl(t, env)
		require.Contains(t, control.Data, "CLASSIFICATION_PENDING")
		require.NotContains(t, control.Data, "{% signal")
	})
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()
	env.RegisterDelayedCallback(func() {
		assigned := queryParentAssignmentStatus(t, env)
		require.Equal(t, StatusAwaitingAcknowledgment, assigned.Status)
		require.Equal(t, DepartmentBilling, assigned.Department)
		require.Equal(t, BillingEmployeeID, assigned.EmployeeID)
		require.Equal(t, "query-001:billing:"+BillingEmployeeID, assigned.AssignmentID)
		require.Contains(t, assigned.ChildWorkflowID, "-child-billing")
		require.Equal(t, AcknowledgmentSignalName, assigned.SignalName)
		require.Equal(t, 10*time.Second, assigned.SLADuration)
		require.NoError(t, env.SignalWorkflowByID(
			assigned.ChildWorkflowID,
			AcknowledgmentSignalName,
			AcknowledgmentSignal{
				TicketID:     ticket.TicketID,
				EmployeeID:   assigned.EmployeeID,
				AssignmentID: assigned.AssignmentID,
			},
		))
	}, time.Second)

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.NoError(t, env.GetWorkflowError())
	require.Equal(t, "CLASSIFICATION_PENDING", pending.Status)
	require.Equal(t, ticket.TicketID, pending.TicketID)
}

func TestParentAssignmentStatusQueryReportsUnroutable(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{TicketID: "query-unroutable-001", Message: "synthetic test ticket"}
	decision := validDecision(DepartmentBilling)
	decision.DepartmentConfidence = 0.1
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.NoError(t, env.GetWorkflowError())
	status := queryParentAssignmentStatus(t, env)
	require.Equal(t, StatusUnroutable, status.Status)
	require.Contains(t, status.Reason, "below 0.65")
	require.Empty(t, status.ChildWorkflowID)
	control := queryAssignmentControl(t, env)
	require.Contains(t, control.Data, "UNROUTABLE")
	require.NotContains(t, control.Data, "{% signal")
}

func TestParentCustomWorkflowControlAcknowledgesChild(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{
		TicketID: "explorer-ack-001",
		Message:  "synthetic billing ticket",
		SLA:      testSLAConfig(10 * time.Second),
	}
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).
		Return(validDecision(DepartmentBilling), nil).Once()
	env.RegisterDelayedCallback(func() {
		control := queryAssignmentControl(t, env)
		require.Equal(t, "formattedData", control.CadenceResponseType)
		require.Equal(t, "text/markdown", control.Format)
		require.Contains(t, control.Data, "Ticket ID:** `explorer-ack-001`")
		require.Contains(t, control.Data, "Department:** `billing`")
		require.Contains(t, control.Data, "Assigned employee:** `"+BillingEmployeeID+"`")
		require.Contains(t, control.Data, "Child Workflow ID:** `")
		require.Contains(t, control.Data, `signalName="`+AcknowledgmentSignalName+`"`)
		require.Contains(t, control.Data, `label="Acknowledge Ticket"`)
		require.Contains(t, control.Data, `workflowId="default-test-workflow-id"`)
		require.Contains(t, control.Data, `input={"ticket_id":"explorer-ack-001","employee_id":"`+BillingEmployeeID+`","assignment_id":"explorer-ack-001:billing:`+BillingEmployeeID+`"}`)

		env.SignalWorkflow(AcknowledgmentSignalName, AcknowledgmentSignal{
			TicketID:     ticket.TicketID,
			EmployeeID:   BillingEmployeeID,
			AssignmentID: assignmentIdentifier(ticket.TicketID, DepartmentBilling, BillingEmployeeID),
		})
	}, time.Second)

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusAcknowledged, result.Status)
	require.True(t, result.SLAMet)
	terminalControl := queryAssignmentControl(t, env)
	require.NotContains(t, terminalControl.Data, "{% signal")
	require.Contains(t, terminalControl.Data, "Current acknowledgment status:** `ACKNOWLEDGED`")
	require.Contains(t, terminalControl.Data, "SLA met:** YES")
}

func TestParentDoesNotForwardSignalAfterChildTimeout(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{
		TicketID: "explorer-race-001",
		Message:  "synthetic billing ticket",
		SLA:      testSLAConfig(time.Second),
	}
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).
		Return(validDecision(DepartmentBilling), nil).Once()
	env.RegisterDelayedCallback(func() {
		env.SignalWorkflow(AcknowledgmentSignalName, AcknowledgmentSignal{
			TicketID:     ticket.TicketID,
			EmployeeID:   BillingEmployeeID,
			AssignmentID: assignmentIdentifier(ticket.TicketID, DepartmentBilling, BillingEmployeeID),
		})
	}, time.Second)

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusSLATimeout, result.Status)
	require.False(t, result.SLAMet)
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

func TestTicketIntakeWorkflowRoutesLayaSelectedAnswerConfidenceAboveThreshold(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{
		TicketID: "synthetic-0027",
		Message:  "Synthetic account merge request.",
		SLA:      testSLAConfig(10 * time.Second),
	}
	decision := validDecision(DepartmentAccount)
	entropy := 0.0506
	decision.DepartmentConfidence = 0.4061
	decision.DepartmentEntropy = &entropy
	decision.DepartmentThreshold = minimumLayaDepartmentConfidence
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()
	env.RegisterDelayedCallback(func() {
		status := queryParentAssignmentStatus(t, env)
		require.Equal(t, StatusAwaitingAcknowledgment, status.Status)
		env.SignalWorkflow(AcknowledgmentSignalName, AcknowledgmentSignal{
			TicketID:     ticket.TicketID,
			EmployeeID:   status.EmployeeID,
			AssignmentID: status.AssignmentID,
		})
	}, time.Second)

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusAcknowledged, result.Status)
	require.Equal(t, DepartmentAccount, result.Department)
	require.NotEmpty(t, result.ChildWorkflowID)
	env.AssertExpectations(t)
}

func TestTicketIntakeWorkflowRejectsLayaSelectedAnswerConfidenceBelowThreshold(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{TicketID: "laya-low-001", Message: "Synthetic ambiguous request."}
	decision := validDecision(DepartmentAccount)
	decision.DepartmentConfidence = 0.3999
	decision.DepartmentThreshold = minimumLayaDepartmentConfidence
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusUnroutable, result.Status)
	require.Contains(t, result.Reason, "below 0.40")
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

func TestRecordedClassificationIsNotCalledAgainOnReplay(t *testing.T) {
	ticket := Ticket{TicketID: "replay-001", Message: "synthetic replay ticket"}
	decision := validDecision(DepartmentBilling)
	decision.DepartmentConfidence = 0.50
	calls := 0
	classificationCallObserver = func() { calls++ }
	t.Cleanup(func() { classificationCallObserver = nil })

	_, err := ClassifyTicket(context.Background(), ticket)
	require.NoError(t, err)
	require.Equal(t, 1, calls, "the observer must count a direct classifier call")

	calls = 0
	replayer := worker.NewWorkflowReplayer()
	replayer.RegisterWorkflow(TicketIntakeWorkflow)
	replayer.RegisterActivity(ClassifyTicket)
	require.NoError(t, replayer.ReplayWorkflowHistory(nil, recordedClassificationHistory(t, ticket, decision)))
	require.Equal(t, 0, calls, "replay must reuse the recorded classification Activity result")
}

func recordedClassificationHistory(t *testing.T, ticket Ticket, decision RoutingDecision) *shared.History {
	t.Helper()
	converter := encoded.GetDefaultDataConverter()
	workflowInput, err := converter.ToData(ticket)
	require.NoError(t, err)
	activityResult, err := converter.ToData(decision)
	require.NoError(t, err)
	result := TicketResult{
		TicketID:       ticket.TicketID,
		Classification: decision,
		Status:         StatusUnroutable,
		Reason:         invalidClassificationReason(decision),
	}
	workflowResult, err := converter.ToData(result)
	require.NoError(t, err)

	taskList := &shared.TaskList{Name: stringPtr("ticket-routing"), Kind: shared.TaskListKindNormal.Ptr()}
	workflowType := &shared.WorkflowType{Name: stringPtr(functionName(TicketIntakeWorkflow))}
	activityType := &shared.ActivityType{Name: stringPtr(functionName(ClassifyTicket))}
	return &shared.History{
		Events: []*shared.HistoryEvent{
			historyEvent(1, shared.EventTypeWorkflowExecutionStarted, &shared.WorkflowExecutionStartedEventAttributes{
				WorkflowType: workflowType,
				TaskList:     taskList,
				Input:        workflowInput,
			}),
			historyEvent(2, shared.EventTypeDecisionTaskScheduled, &shared.DecisionTaskScheduledEventAttributes{}),
			historyEvent(3, shared.EventTypeDecisionTaskStarted, &shared.DecisionTaskStartedEventAttributes{}),
			historyEvent(4, shared.EventTypeDecisionTaskCompleted, &shared.DecisionTaskCompletedEventAttributes{
				ScheduledEventId: int64Ptr(2),
				StartedEventId:   int64Ptr(3),
			}),
			historyEvent(5, shared.EventTypeActivityTaskScheduled, &shared.ActivityTaskScheduledEventAttributes{
				ActivityId:   stringPtr("0"),
				ActivityType: activityType,
				TaskList:     taskList,
				Input:        workflowInput,
			}),
			historyEvent(6, shared.EventTypeActivityTaskStarted, &shared.ActivityTaskStartedEventAttributes{
				ScheduledEventId: int64Ptr(5),
			}),
			historyEvent(7, shared.EventTypeActivityTaskCompleted, &shared.ActivityTaskCompletedEventAttributes{
				ScheduledEventId: int64Ptr(5),
				StartedEventId:   int64Ptr(6),
				Result:           activityResult,
			}),
			historyEvent(8, shared.EventTypeDecisionTaskScheduled, &shared.DecisionTaskScheduledEventAttributes{}),
			historyEvent(9, shared.EventTypeDecisionTaskStarted, &shared.DecisionTaskStartedEventAttributes{}),
			historyEvent(10, shared.EventTypeDecisionTaskCompleted, &shared.DecisionTaskCompletedEventAttributes{
				ScheduledEventId: int64Ptr(8),
				StartedEventId:   int64Ptr(9),
			}),
			historyEvent(11, shared.EventTypeWorkflowExecutionCompleted, &shared.WorkflowExecutionCompletedEventAttributes{
				Result:                       workflowResult,
				DecisionTaskCompletedEventId: int64Ptr(10),
			}),
		},
	}
}

func historyEvent(eventID int64, eventType shared.EventType, attributes interface{}) *shared.HistoryEvent {
	event := &shared.HistoryEvent{
		EventId:   int64Ptr(eventID),
		EventType: eventType.Ptr(),
	}
	switch attributes := attributes.(type) {
	case *shared.WorkflowExecutionStartedEventAttributes:
		event.WorkflowExecutionStartedEventAttributes = attributes
	case *shared.DecisionTaskScheduledEventAttributes:
		event.DecisionTaskScheduledEventAttributes = attributes
	case *shared.DecisionTaskStartedEventAttributes:
		event.DecisionTaskStartedEventAttributes = attributes
	case *shared.DecisionTaskCompletedEventAttributes:
		event.DecisionTaskCompletedEventAttributes = attributes
	case *shared.ActivityTaskScheduledEventAttributes:
		event.ActivityTaskScheduledEventAttributes = attributes
	case *shared.ActivityTaskStartedEventAttributes:
		event.ActivityTaskStartedEventAttributes = attributes
	case *shared.ActivityTaskCompletedEventAttributes:
		event.ActivityTaskCompletedEventAttributes = attributes
	case *shared.WorkflowExecutionCompletedEventAttributes:
		event.WorkflowExecutionCompletedEventAttributes = attributes
	default:
		panic("unsupported history attributes")
	}
	return event
}

func functionName(fn interface{}) string {
	return runtime.FuncForPC(reflect.ValueOf(fn).Pointer()).Name()
}

func stringPtr(value string) *string { return &value }

func int64Ptr(value int64) *int64 { return &value }

func TestJevClassifierSendsAndParsesDocumentedContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "/v1/systemone", request.URL.Path)
		require.Equal(t, http.MethodPost, request.Method)
		require.Equal(t, "Bearer test-api-key", request.Header.Get("Authorization"))
		require.Equal(t, "application/json", request.Header.Get("Content-Type"))

		var payload jevRequest
		require.NoError(t, json.NewDecoder(request.Body).Decode(&payload))
		require.Equal(t, "A synthetic billing request.", payload.State)
		require.Equal(t, testJevModel, payload.Model)
		require.Len(t, payload.Questions, 3)
		requireChoiceQuestion(t, payload.Questions["department"], []string{"billing", "technical", "account", "content"})
		requireChoiceQuestion(t, payload.Questions["priority"], []string{"low", "normal", "high", "critical"})
		requireChoiceQuestion(t, payload.Questions["complexity"], []string{"tier1", "tier2", "tier3"})

		writer.Header().Set("Content-Type", "application/json")
		_, err := io.WriteString(writer, validJevResponse)
		require.NoError(t, err)
	}))
	defer server.Close()

	classifier, err := newSystemOneClassifier("test-api-key", server.URL+"/v1/systemone", testJevModel, classifierProviderTypeSafe, server.Client())
	require.NoError(t, err)
	inferenceStarted := time.Date(2026, time.October, 10, 12, 0, 0, 0, time.UTC)
	inferenceFinished := inferenceStarted.Add(25 * time.Millisecond)
	clockValues := []time.Time{inferenceStarted, inferenceFinished}
	classifier.now = func() time.Time {
		require.NotEmpty(t, clockValues)
		current := clockValues[0]
		clockValues = clockValues[1:]
		return current
	}
	decision, err := classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "jev-001", Message: "A synthetic billing request."})

	require.NoError(t, err)
	require.Empty(t, clockValues)
	require.Equal(t, DepartmentBilling, decision.Department)
	require.Equal(t, PriorityHigh, decision.Priority)
	require.Equal(t, ComplexityTier2, decision.Complexity)
	require.InDelta(t, 0.81, decision.DepartmentConfidence, 0.0001)
	require.InDelta(t, 0.72, decision.PriorityConfidence, 0.0001)
	require.InDelta(t, 0.68, decision.ComplexityConfidence, 0.0001)
	require.Nil(t, decision.DepartmentEntropy)
	require.Equal(t, minimumDepartmentConfidence, decision.DepartmentThreshold)
	require.InDelta(t, 0.88, decision.DepartmentProbabilities["billing"], 0.0001)
	require.Equal(t, "jev-1.13.0", decision.Model)
	require.Equal(t, 412, decision.InputTokens)
	require.Equal(t, 47, decision.OutputTokens)
	require.True(t, decision.TokenUsageReported)
	require.Equal(t, 25*time.Millisecond, decision.InferenceLatency)
	require.False(t, decision.PriorityUncertain)
	require.False(t, decision.ComplexityUncertain)
}

func TestLayaClassifierSendsNoAuthorizationHeader(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Empty(t, request.Header.Get("Authorization"))
		var payload jevRequest
		require.NoError(t, json.NewDecoder(request.Body).Decode(&payload))
		require.Equal(t, "convaiinnovations/laya", payload.Model)
		_, err := io.WriteString(writer, validLayaResponse())
		require.NoError(t, err)
	}))
	defer server.Close()

	classifier, err := newSystemOneClassifier("", server.URL, "convaiinnovations/laya", classifierProviderLaya, server.Client())
	require.NoError(t, err)
	decision, err := classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "laya-001", Message: "A synthetic billing request."})
	require.NoError(t, err)
	require.Equal(t, DepartmentBilling, decision.Department)
}

func TestLayaClassifierUsesSelectedAnswerConfidenceForRouting(t *testing.T) {
	response := layaResponseWithDepartmentConfidence(0.0506, 0.4061)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		_, err := io.WriteString(writer, response)
		require.NoError(t, err)
	}))
	defer server.Close()

	classifier, err := newSystemOneClassifier(
		"",
		server.URL,
		"convaiinnovations/laya",
		classifierProviderLaya,
		server.Client(),
	)
	require.NoError(t, err)
	decision, err := classifier.ClassifyTicket(
		t.Context(),
		Ticket{TicketID: "synthetic-0027", Message: "Synthetic account merge request."},
	)

	require.NoError(t, err)
	require.Equal(t, DepartmentAccount, decision.Department)
	require.InDelta(t, 0.4061, decision.DepartmentConfidence, 0.000001)
	require.NotNil(t, decision.DepartmentEntropy)
	require.InDelta(t, 0.0506, *decision.DepartmentEntropy, 0.000001)
	require.Equal(t, minimumLayaDepartmentConfidence, decision.DepartmentThreshold)
	require.Empty(t, invalidClassificationReason(decision))
}

func TestLocalLayaCorpus(t *testing.T) {
	if os.Getenv("TICKET_ROUTING_LAYA_CORPUS") != "1" {
		t.Skip("set TICKET_ROUTING_LAYA_CORPUS=1 to run the 40-ticket local Laya corpus")
	}
	config, err := loadClassifier(defaultCatalogDir, "laya-local")
	require.NoError(t, err)
	classifier, err := newSystemOneClassifier(
		"",
		config.Endpoint,
		config.Model,
		config.Provider,
		&http.Client{Timeout: 20 * time.Second},
	)
	require.NoError(t, err)

	file, err := os.Open(filepath.Join("..", "..", "testdata", "tickets.jsonl"))
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, file.Close()) })

	counts := map[float64]int{0.65: 0, 0.50: 0, 0.40: 0}
	matchesAtLayaThreshold := 0
	total := 0
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var fixture struct {
			Ticket
			ExpectedDepartment Department `json:"expected_department"`
		}
		require.NoError(t, json.Unmarshal(scanner.Bytes(), &fixture))
		decision, classifyErr := classifier.ClassifyTicket(t.Context(), fixture.Ticket)
		require.NoError(t, classifyErr, fixture.TicketID)
		total++
		for threshold := range counts {
			if decision.DepartmentConfidence >= threshold {
				counts[threshold]++
			}
		}
		if decision.DepartmentConfidence >= minimumLayaDepartmentConfidence &&
			decision.Department == fixture.ExpectedDepartment {
			matchesAtLayaThreshold++
		}
		t.Logf(
			"%s expected=%s selected=%s answer_confidence=%.4f entropy_confidence=%.4f",
			fixture.TicketID,
			fixture.ExpectedDepartment,
			decision.Department,
			decision.DepartmentConfidence,
			*decision.DepartmentEntropy,
		)
	}
	require.NoError(t, scanner.Err())
	require.Equal(t, 40, total)
	t.Logf(
		"Laya corpus: total=%d routable>=0.65=%d routable>=0.50=%d routable>=0.40=%d label_matches_at_0.40=%d",
		total,
		counts[0.65],
		counts[0.50],
		counts[0.40],
		matchesAtLayaThreshold,
	)
}

func TestConfiguredClassifierRequiresKeyOnlyForTypeSafe(t *testing.T) {
	catalogDir := writeTestCatalog(t, testCatalog)

	t.Setenv("CLASSIFIER_AI_KEY", "")
	_, _, err := configuredClassifier("live", "jev-default", catalogDir)
	require.Error(t, err)
	require.Contains(t, err.Error(), "CLASSIFIER_AI_KEY")

	selected, classifier, err := configuredClassifier("live", "laya-local", catalogDir)
	require.NoError(t, err)
	require.Equal(t, "laya-local", selected)
	require.NotNil(t, classifier)

	t.Setenv("CLASSIFIER_AI_KEY", "test-api-key")
	_, classifier, err = configuredClassifier("live", "jev-default", catalogDir)
	require.NoError(t, err)
	require.NotNil(t, classifier)
}

func TestLoadClassifierFromRepositoryCatalog(t *testing.T) {
	jev, err := loadClassifier(defaultCatalogDir, "jev-default")
	require.NoError(t, err)
	require.Equal(t, classifierProviderTypeSafe, jev.Provider)

	laya, err := loadClassifier(defaultCatalogDir, "laya-local")
	require.NoError(t, err)
	require.Equal(t, classifierProviderLaya, laya.Provider)
	require.Equal(t, "convaiinnovations/laya", laya.Model)
}

func TestMutatedCatalogEndpointReachesClassifierTransport(t *testing.T) {
	requestTargets := make(chan [2]string, 1)
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		var payload struct {
			Model string `json:"model"`
		}
		require.NoError(t, json.NewDecoder(request.Body).Decode(&payload))
		requestTargets <- [2]string{request.URL.Path, payload.Model}
		writer.Header().Set("Content-Type", "application/json")
		_, err := io.WriteString(writer, validJevResponse)
		require.NoError(t, err)
	}))
	defer server.Close()

	catalog := fmt.Sprintf(`classifiers:
  - id: changed-jev
    provider: typesafe
    model: changed-model
    endpoint: %s/changed-systemone
`, server.URL)
	selected, err := loadClassifier(writeTestCatalog(t, catalog), "changed-jev")
	require.NoError(t, err)

	classifier, err := newSystemOneClassifier(
		"test-api-key",
		selected.Endpoint,
		selected.Model,
		selected.Provider,
		server.Client(),
	)
	require.NoError(t, err)
	decision, err := classifier.ClassifyTicket(
		context.Background(),
		Ticket{TicketID: "endpoint-mutation-001", Message: "Synthetic request."},
	)
	require.NoError(t, err)
	require.NotEmpty(t, decision.Model)
	require.Equal(t, [2]string{"/changed-systemone", "changed-model"}, <-requestTargets)
}

func TestLoadClassifierRejectsInvalidCatalogs(t *testing.T) {
	tests := []struct {
		name     string
		catalog  string
		id       string
		contains string
	}{
		{name: "unknown ID", catalog: testCatalog, id: "missing", contains: "unknown classifier ID"},
		{name: "candidate provider", catalog: testCatalog, id: "kev-local", contains: "catalog candidates"},
		{name: "missing field", catalog: "classifiers:\n  - id: laya-local\n    provider: laya\n    model: m\n", id: "laya-local", contains: `missing "endpoint"`},
		{name: "duplicate ID", catalog: testCatalog + "  - id: laya-local\n    provider: laya\n    model: m\n    endpoint: http://localhost:8008/v1/systemone\n", id: "laya-local", contains: "duplicate"},
		{name: "remote Laya", catalog: "classifiers:\n  - id: laya-remote\n    provider: laya\n    model: m\n    endpoint: https://laya.example/v1/systemone\n", id: "laya-remote", contains: "loopback"},
		{name: "plain HTTP TypeSafe", catalog: "classifiers:\n  - id: jev-http\n    provider: typesafe\n    model: m\n    endpoint: http://api.typesafe.ai/v1/systemone\n", id: "jev-http", contains: "HTTPS"},
		{name: "endpoint credentials", catalog: "classifiers:\n  - id: laya-creds\n    provider: laya\n    model: m\n    endpoint: http://user:pass@localhost:8008/v1/systemone\n", id: "laya-creds", contains: "without credentials"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := loadClassifier(writeTestCatalog(t, test.catalog), test.id)
			require.Error(t, err)
			require.Contains(t, err.Error(), test.contains)
		})
	}
}

const testJevModel = "jev-latest"

const testCatalog = `classifiers:
  - id: jev-default
    provider: typesafe
    model: jev-latest
    endpoint: https://api.typesafe.ai/v1/systemone
  - id: laya-local
    provider: laya
    model: convaiinnovations/laya
    endpoint: http://localhost:8008/v1/systemone
  - id: kev-local
    provider: kev
    model: kev-latest
    endpoint: http://localhost:8008/v1/systemone
`

func writeTestCatalog(t *testing.T, contents string) string {
	t.Helper()
	directory := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(directory, "classifiers.yaml"), []byte(contents), 0o600))
	return directory
}

func TestJevClassifierRecordsTimingWhenUsageIsMissing(t *testing.T) {
	var response jevResponse
	require.NoError(t, json.Unmarshal([]byte(validJevResponse), &response))
	response.Usage = nil
	body, err := json.Marshal(response)
	require.NoError(t, err)

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		time.Sleep(5 * time.Millisecond)
		writer.Header().Set("Content-Type", "application/json")
		_, writeErr := writer.Write(body)
		require.NoError(t, writeErr)
	}))
	defer server.Close()

	classifier, err := newSystemOneClassifier("test-api-key", server.URL, testJevModel, classifierProviderTypeSafe, server.Client())
	require.NoError(t, err)
	decision, err := classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "missing-usage-001", Message: "Synthetic request."})
	require.NoError(t, err)
	require.GreaterOrEqual(t, decision.InferenceLatency, 5*time.Millisecond)
	require.False(t, decision.TokenUsageReported)
	require.Zero(t, decision.InputTokens)
	require.Zero(t, decision.OutputTokens)
}

func TestJevClassifierMarksInformationalLowConfidenceAsUncertain(t *testing.T) {
	response := validJevResponseWithConfidence(0.40, 0.30)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		_, err := io.WriteString(writer, response)
		require.NoError(t, err)
	}))
	defer server.Close()

	classifier, err := newSystemOneClassifier("test-api-key", server.URL, testJevModel, classifierProviderTypeSafe, server.Client())
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

			classifier, err := newSystemOneClassifier("test-api-key", server.URL, testJevModel, classifierProviderTypeSafe, server.Client())
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

	classifier, err := newSystemOneClassifier("test-api-key", endpoint, testJevModel, classifierProviderTypeSafe, &http.Client{Timeout: 100 * time.Millisecond})
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

			classifier, err := newSystemOneClassifier("test-api-key", server.URL, testJevModel, classifierProviderTypeSafe, server.Client())
			require.NoError(t, err)
			_, err = classifier.ClassifyTicket(t.Context(), Ticket{TicketID: "malformed-001", Message: "Synthetic request."})
			require.Error(t, err)
			var customError *cadence.CustomError
			require.ErrorAs(t, err, &customError)
			require.Equal(t, errReasonJevMalformedResponse, customError.Reason())
		})
	}
}

func TestLayaClassifierRejectsMalformedOrInconsistentConfidence(t *testing.T) {
	inconsistent := mustDecodeJevResponse(validLayaResponse())
	inconsistentValue := 0.40
	department := inconsistent.Answers["department"]
	department.AnswerConfidence = &inconsistentValue
	inconsistent.Answers["department"] = department

	outOfRange := mustDecodeJevResponse(validLayaResponse())
	outOfRangeValue := 1.1
	department = outOfRange.Answers["department"]
	department.AnswerConfidence = &outOfRangeValue
	outOfRange.Answers["department"] = department

	tests := []struct {
		name string
		body string
	}{
		{name: "missing answer confidence", body: validJevResponse},
		{name: "selected probability mismatch", body: mustEncodeJevResponse(inconsistent)},
		{name: "answer confidence out of range", body: mustEncodeJevResponse(outOfRange)},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				_, err := io.WriteString(writer, test.body)
				require.NoError(t, err)
			}))
			defer server.Close()

			classifier, err := newSystemOneClassifier(
				"",
				server.URL,
				"convaiinnovations/laya",
				classifierProviderLaya,
				server.Client(),
			)
			require.NoError(t, err)
			_, err = classifier.ClassifyTicket(
				t.Context(),
				Ticket{TicketID: "malformed-laya-001", Message: "Synthetic request."},
			)
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

func validLayaResponse() string {
	response := mustDecodeJevResponse(validJevResponse)
	for name, answer := range response.Answers {
		selectedConfidence := answer.Probabilities[answer.Choice]
		answer.AnswerConfidence = &selectedConfidence
		response.Answers[name] = answer
	}
	return mustEncodeJevResponse(response)
}

func layaResponseWithDepartmentConfidence(entropyConfidence, answerConfidence float64) string {
	response := mustDecodeJevResponse(validLayaResponse())
	department := response.Answers["department"]
	department.Choice = string(DepartmentAccount)
	department.Probabilities = map[string]float64{
		string(DepartmentBilling):   0.20,
		string(DepartmentTechnical): 0.1939,
		string(DepartmentAccount):   answerConfidence,
		string(DepartmentContent):   0.20,
	}
	department.Confidence = &entropyConfidence
	department.AnswerConfidence = &answerConfidence
	response.Answers["department"] = department
	return mustEncodeJevResponse(response)
}

func mustDecodeJevResponse(body string) jevResponse {
	var response jevResponse
	if err := json.Unmarshal([]byte(body), &response); err != nil {
		panic(err)
	}
	return response
}

func mustEncodeJevResponse(response jevResponse) string {
	body, err := json.Marshal(response)
	if err != nil {
		panic(err)
	}
	return string(body)
}

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

func queryParentAssignmentStatus(t *testing.T, env *testsuite.TestWorkflowEnvironment) ParentAssignmentStatus {
	t.Helper()
	value, err := env.QueryWorkflow(ParentAssignmentQueryName)
	require.NoError(t, err)
	var response ParentAssignmentStatus
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
