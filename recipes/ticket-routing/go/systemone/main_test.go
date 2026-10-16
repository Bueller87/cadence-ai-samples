package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
	"go.uber.org/cadence/.gen/go/shared"
	"go.uber.org/cadence/client"
	cadencemocks "go.uber.org/cadence/mocks"
)

func TestTicketForStartSelectsBundledFixture(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tickets.jsonl")
	require.NoError(t, os.WriteFile(path, []byte(
		"{\"ticket_id\":\"fixture-001\",\"message\":\"First synthetic request.\",\"expected_department\":\"billing\"}\n"+
			"{\"ticket_id\":\"fixture-002\",\"message\":\"Second synthetic request.\",\"expected_department\":\"technical\"}\n",
	), 0o600))

	ticket, err := ticketForStart("", path, func(length int) (int, error) {
		require.Equal(t, 2, length)
		return 1, nil
	})

	require.NoError(t, err)
	require.Equal(t, "fixture-002", ticket.TicketID)
	require.Equal(t, "Second synthetic request.", ticket.Message)
	require.Equal(t, SLAConfig{}, ticket.SLA)
}

func TestTicketForStartTextOverridesDataset(t *testing.T) {
	ticket, err := ticketForStart("  Reproduce this custom request.  ", filepath.Join(t.TempDir(), "missing"), func(int) (int, error) {
		t.Fatal("selector must not run for a text override")
		return 0, nil
	})

	require.NoError(t, err)
	require.Equal(t, "Reproduce this custom request.", ticket.Message)
	require.Equal(t, "custom-bde28e6cc7af", ticket.TicketID)
}

func TestCommandUsesOnlyWorkerAndStartActions(t *testing.T) {
	worker, err := parseCommand([]string{"worker", "-mode", "mock"})
	require.NoError(t, err)
	require.Equal(t, "worker", worker.Action)
	require.Equal(t, "mock", worker.Mode)

	start, err := parseCommand([]string{"start", "-mode", "live"})
	require.NoError(t, err)
	require.Equal(t, defaultWorkflowID, start.WorkflowID)
	require.Empty(t, start.Text)
	require.Empty(t, start.ClassifierID)
	require.Empty(t, start.CatalogDir)
	require.False(t, start.ConfirmLive)

	override, err := parseCommand([]string{"start", "-mode", "mock", "-text", "hello", "-workflow-id", "custom-workflow"})
	require.NoError(t, err)
	require.Equal(t, "hello", override.Text)
	require.Equal(t, "custom-workflow", override.WorkflowID)

	for _, args := range [][]string{
		{},
		{"batch"},
		{"acknowledge"},
		{"worker"},
		{"start"},
		{"worker", "-mode", "unknown"},
		{"worker", "-mode", "live"},
		{"start", "-mode", "live", "-classifier-id", "laya-local"},
		{"start", "-mode", "mock", "-manual-sla", "1s"},
		{"-mode", "worker"},
	} {
		_, err := parseCommand(args)
		require.Errorf(t, err, "expected %v to fail", args)
	}
	_, err = parseCommand([]string{
		"worker", "-mode", "live", "-classifier-id", "laya-local", "-confirm-live",
	})
	require.NoError(t, err)
}

func TestDefaultSLAsAllowInteractiveAcknowledgment(t *testing.T) {
	require.Equal(t, 2*time.Minute, defaultSLAConfig().Critical)
	require.Equal(t, 3*time.Minute, defaultSLAConfig().High)
	require.Equal(t, 4*time.Minute, defaultSLAConfig().Normal)
	require.Equal(t, 5*time.Minute, defaultSLAConfig().Low)
}

func TestWaitForParentAssignmentPollsUntilAssigned(t *testing.T) {
	calls := 0
	status, err := waitForParentAssignmentWithQuery(
		context.Background(),
		func(context.Context) (ParentAssignmentStatus, error) {
			calls++
			if calls < 3 {
				return ParentAssignmentStatus{Status: "CLASSIFICATION_PENDING"}, nil
			}
			return ParentAssignmentStatus{
				Status:          StatusAwaitingAcknowledgment,
				ChildWorkflowID: "ticket-routing-demo-child-billing",
			}, nil
		},
		time.Nanosecond,
		time.Second,
	)

	require.NoError(t, err)
	require.Equal(t, 3, calls)
	require.Equal(t, "ticket-routing-demo-child-billing", status.ChildWorkflowID)
}

func TestWaitForParentAssignmentIsBounded(t *testing.T) {
	_, err := waitForParentAssignmentWithQuery(
		context.Background(),
		func(context.Context) (ParentAssignmentStatus, error) {
			return ParentAssignmentStatus{}, errors.New("query not registered")
		},
		time.Nanosecond,
		time.Nanosecond,
	)

	require.ErrorContains(t, err, "query not registered")
}

func TestStartPrintsAssignmentBeforeTerminalWait(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	run := cadencemocks.NewWorkflowRun(t)
	assignmentReady := make(chan struct{})
	run.On("GetID").Return(defaultWorkflowID)
	run.On("GetRunID").Return("run-001")
	run.On("Get", mock.Anything, mock.Anything).Run(func(arguments mock.Arguments) {
		<-assignmentReady
		result := arguments.Get(1).(*TicketResult)
		*result = TicketResult{
			TicketID: "fixture-001",
			Classification: RoutingDecision{
				Department: DepartmentBilling,
				Priority:   PriorityHigh,
				Complexity: ComplexityTier2,
			},
			Department:   DepartmentBilling,
			EmployeeID:   BillingEmployeeID,
			AssignmentID: "fixture-001:billing:" + BillingEmployeeID,
			Status:       StatusAcknowledged,
			SLAMet:       true,
		}
	}).Return(nil).Once()

	ticket := Ticket{TicketID: "fixture-001", Message: "I was charged twice."}
	cadenceClient.On(
		"ExecuteWorkflow",
		mock.Anything,
		mock.MatchedBy(func(options client.StartWorkflowOptions) bool {
			return options.ID == defaultWorkflowID &&
				options.TaskList == TaskList &&
				options.WorkflowIDReusePolicy == client.WorkflowIDReusePolicyAllowDuplicate
		}),
		mock.Anything,
		ticket,
	).Return(run, nil).Once()

	waiter := func(context.Context, string, string) (ParentAssignmentStatus, error) {
		close(assignmentReady)
		return ParentAssignmentStatus{
			Status:          StatusAwaitingAcknowledgment,
			TicketID:        ticket.TicketID,
			Department:      DepartmentBilling,
			EmployeeID:      BillingEmployeeID,
			AssignmentID:    "fixture-001:billing:" + BillingEmployeeID,
			ChildWorkflowID: defaultWorkflowID + "-child-billing",
			SignalName:      AcknowledgmentSignalName,
			SLADuration:     3 * time.Minute,
		}, nil
	}

	var output bytes.Buffer
	err := startWithWaiter(context.Background(), cadenceClient, TaskList, defaultWorkflowID, ticket, &output, waiter)

	require.NoError(t, err)
	text := output.String()
	require.Contains(t, text, "selected ticket ID: fixture-001")
	require.Contains(t, text, "child workflow ID: ticket-routing-demo-child-billing")
	require.Contains(t, text, "employee ID: "+BillingEmployeeID)
	require.Contains(t, text, "assignment ID: fixture-001:billing:"+BillingEmployeeID)
	require.Contains(t, text, "signal name: "+AcknowledgmentSignalName)
	require.Contains(t, text, "acknowledgment SLA: 3m0s")
	require.Less(t, strings.Index(text, "classified assignment"), strings.Index(text, "waiting for workflow result"))
	require.Contains(t, text, "Business status: ACKNOWLEDGED")
}

func TestStartPrintsAssignmentWhenTerminalResultWinsPollingRace(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	run := cadencemocks.NewWorkflowRun(t)
	run.On("GetID").Return(defaultWorkflowID)
	run.On("GetRunID").Return("run-fast")
	run.On("Get", mock.Anything, mock.Anything).Run(func(arguments mock.Arguments) {
		result := arguments.Get(1).(*TicketResult)
		*result = TicketResult{
			TicketID:        "fixture-fast",
			Department:      DepartmentBilling,
			EmployeeID:      BillingEmployeeID,
			AssignmentID:    "fixture-fast:billing:" + BillingEmployeeID,
			ChildWorkflowID: defaultWorkflowID + "-child-billing",
			SLADuration:     3 * time.Minute,
			Status:          StatusAcknowledged,
			SLAMet:          true,
		}
	}).Return(nil).Once()
	ticket := Ticket{TicketID: "fixture-fast", Message: "Synthetic request."}
	cadenceClient.On(
		"ExecuteWorkflow", mock.Anything, mock.Anything, mock.Anything, ticket,
	).Return(run, nil).Once()

	var output bytes.Buffer
	err := startWithWaiter(
		context.Background(),
		cadenceClient,
		TaskList,
		defaultWorkflowID,
		ticket,
		&output,
		func(ctx context.Context, _, _ string) (ParentAssignmentStatus, error) {
			<-ctx.Done()
			return ParentAssignmentStatus{}, ctx.Err()
		},
	)

	require.NoError(t, err)
	require.Contains(t, output.String(), "child workflow ID: ticket-routing-demo-child-billing")
	require.Contains(t, output.String(), "assignment ID: fixture-fast:billing:"+BillingEmployeeID)
	require.Contains(t, output.String(), "signal name: "+AcknowledgmentSignalName)
	require.Contains(t, output.String(), "acknowledgment SLA: 3m0s")
}

func TestStartStopsPollingWhenWorkflowFails(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	run := cadencemocks.NewWorkflowRun(t)
	run.On("GetID").Return(defaultWorkflowID)
	run.On("GetRunID").Return("run-failed")
	run.On("Get", mock.Anything, mock.Anything).
		Return(errors.New("classification failed")).Once()
	ticket := Ticket{TicketID: "fixture-failed", Message: "Synthetic request."}
	cadenceClient.On(
		"ExecuteWorkflow", mock.Anything, mock.Anything, mock.Anything, ticket,
	).Return(run, nil).Once()

	pollingStopped := make(chan struct{})
	waiter := func(ctx context.Context, _, _ string) (ParentAssignmentStatus, error) {
		<-ctx.Done()
		close(pollingStopped)
		return ParentAssignmentStatus{}, ctx.Err()
	}
	err := startWithWaiter(
		context.Background(), cadenceClient, TaskList, defaultWorkflowID,
		ticket, &bytes.Buffer{}, waiter,
	)

	require.ErrorContains(t, err, "classification failed")
	select {
	case <-pollingStopped:
	case <-time.After(time.Second):
		t.Fatal("assignment polling did not stop after terminal failure")
	}
}

func TestStartReportsDuplicateOpenRun(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	cadenceClient.On(
		"ExecuteWorkflow", mock.Anything, mock.Anything, mock.Anything, mock.Anything,
	).Return(nil, &shared.WorkflowExecutionAlreadyStartedError{}).Once()

	err := startWithWaiter(
		context.Background(),
		cadenceClient,
		TaskList,
		defaultWorkflowID,
		Ticket{TicketID: "fixture-001", Message: "Synthetic request."},
		&bytes.Buffer{},
		func(context.Context, string, string) (ParentAssignmentStatus, error) {
			t.Fatal("waiter must not run after a duplicate start")
			return ParentAssignmentStatus{}, nil
		},
	)

	require.ErrorContains(t, err, "already has an open Run")
	require.ErrorContains(t, err, "different -workflow-id")
}
