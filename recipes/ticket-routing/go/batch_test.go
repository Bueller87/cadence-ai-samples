package main

import (
	"context"
	"fmt"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestLoadBatchTicketsValidatesAndRepeatsDataset(t *testing.T) {
	tickets, err := loadBatchTickets(filepath.Join("..", "testdata", "tickets.jsonl"), 45, time.Second)
	require.NoError(t, err)
	require.Len(t, tickets, 45)

	seenIDs := make(map[string]struct{})
	for _, ticket := range tickets {
		_, duplicate := seenIDs[ticket.TicketID]
		require.False(t, duplicate)
		seenIDs[ticket.TicketID] = struct{}{}
		require.Equal(t, time.Second, ticket.SLA.Critical)
		require.Equal(t, time.Second, ticket.SLA.High)
		require.Equal(t, time.Second, ticket.SLA.Normal)
		require.Equal(t, time.Second, ticket.SLA.Low)
	}
	require.Equal(t, tickets[0].Message, tickets[40].Message)
}

func TestValidateDatasetTicketRejectsInvalidValues(t *testing.T) {
	seenIDs := make(map[string]struct{})
	err := validateDatasetTicket(datasetTicket{
		TicketID:           "invalid-001",
		Message:            "synthetic request",
		ExpectedDepartment: "unsupported",
		ExpectedPriority:   PriorityNormal,
		ExpectedComplexity: ComplexityTier1,
	}, seenIDs)
	require.Error(t, err)
}

func TestNewBatchJobsCreatesUniqueWorkflowIDs(t *testing.T) {
	tickets := []Ticket{
		{TicketID: "batch-000001-synthetic-0001"},
		{TicketID: "batch-000002-synthetic-0001"},
	}
	jobs := newBatchJobs(tickets, "run-001")
	require.Len(t, jobs, 2)
	require.NotEqual(t, jobs[0].WorkflowID, jobs[1].WorkflowID)
	require.Contains(t, jobs[0].WorkflowID, "run-001")
}

func TestRepeatedBatchRunsCreateDifferentChildWorkflowIDs(t *testing.T) {
	tickets := []Ticket{{TicketID: "batch-000001-synthetic-0001"}}
	firstRun := newBatchJobs(tickets, "run-001")
	secondRun := newBatchJobs(tickets, "run-002")

	require.Equal(t, firstRun[0].Ticket.TicketID, secondRun[0].Ticket.TicketID)
	require.NotEqual(t,
		childWorkflowID(firstRun[0].WorkflowID, DepartmentBilling),
		childWorkflowID(secondRun[0].WorkflowID, DepartmentBilling),
	)
}

func TestDifferentTicketsInBatchCreateUniqueChildWorkflowIDs(t *testing.T) {
	tickets := []Ticket{
		{TicketID: "batch-000001-synthetic-0001"},
		{TicketID: "batch-000002-synthetic-0002"},
	}
	jobs := newBatchJobs(tickets, "run-001")

	require.NotEqual(t,
		childWorkflowID(jobs[0].WorkflowID, DepartmentBilling),
		childWorkflowID(jobs[1].WorkflowID, DepartmentBilling),
	)
}

func TestChildWorkflowIDIsStableForParentReplay(t *testing.T) {
	parentID := "ticket-routing-batch-run-001-batch-000001-synthetic-0001"
	firstDecision := childWorkflowID(parentID, DepartmentTechnical)
	replayedDecision := childWorkflowID(parentID, DepartmentTechnical)

	require.Equal(t, firstDecision, replayedDecision)
	require.Equal(t, parentID+"-child-technical", firstDecision)
}

func TestRunBoundedBatchEnforcesConcurrencyLimit(t *testing.T) {
	jobs := make([]batchJob, 20)
	for index := range jobs {
		jobs[index] = batchJob{Ticket: Ticket{TicketID: fmt.Sprintf("ticket-%d", index)}}
	}

	var active atomic.Int32
	var maximumActive atomic.Int32
	summary := runBoundedBatch(context.Background(), jobs, 3, func(_ context.Context, job batchJob) (TicketResult, error) {
		current := active.Add(1)
		defer active.Add(-1)
		for {
			observed := maximumActive.Load()
			if current <= observed || maximumActive.CompareAndSwap(observed, current) {
				break
			}
		}
		time.Sleep(5 * time.Millisecond)
		return TicketResult{TicketID: job.Ticket.TicketID, Department: DepartmentBilling, Status: StatusSLATimeout}, nil
	})

	require.Equal(t, 20, summary.TotalSubmitted)
	require.Equal(t, 20, summary.Completed)
	require.Zero(t, summary.Failed)
	require.LessOrEqual(t, maximumActive.Load(), int32(3))
	require.LessOrEqual(t, summary.PeakInFlight, 3)
	// The work sleeps long enough that sequential execution would be materially slower.
	require.GreaterOrEqual(t, maximumActive.Load(), int32(2))
	require.GreaterOrEqual(t, summary.PeakInFlight, 2)
}

func TestRunBoundedBatchAggregatesFailuresAndResults(t *testing.T) {
	jobs := []batchJob{
		{WorkflowID: "workflow-1", Ticket: Ticket{TicketID: "ticket-1"}},
		{WorkflowID: "workflow-2", Ticket: Ticket{TicketID: "ticket-2"}},
		{WorkflowID: "workflow-3", Ticket: Ticket{TicketID: "ticket-3"}},
		{WorkflowID: "workflow-4", Ticket: Ticket{TicketID: "ticket-4"}},
	}
	summary := runBoundedBatch(context.Background(), jobs, 2, func(_ context.Context, job batchJob) (TicketResult, error) {
		switch job.Ticket.TicketID {
		case "ticket-1":
			return TicketResult{Status: StatusUnroutable}, nil
		case "ticket-2":
			return TicketResult{Department: DepartmentTechnical, Status: StatusAcknowledged}, nil
		case "ticket-3":
			return TicketResult{Department: DepartmentContent, Status: StatusSLATimeout}, nil
		default:
			return TicketResult{}, fmt.Errorf("synthetic workflow failure")
		}
	})

	require.Equal(t, 4, summary.TotalSubmitted)
	require.Equal(t, 3, summary.Completed)
	require.Equal(t, 1, summary.Failed)
	require.Equal(t, 1, summary.Unroutable)
	require.Equal(t, 1, summary.Acknowledged)
	require.Equal(t, 1, summary.SLATimeout)
	require.Equal(t, 1, summary.ByDepartment[DepartmentTechnical])
	require.Equal(t, 1, summary.ByDepartment[DepartmentContent])
	require.Len(t, summary.Failures, 1)
	require.Equal(t, "workflow-4", summary.Failures[0].WorkflowID)
	require.Equal(t, "ticket-4", summary.Failures[0].TicketID)
	require.Contains(t, summary.Failures[0].Error, "synthetic workflow failure")
	require.False(t, summary.Failures[0].StartedAt.IsZero())
	require.GreaterOrEqual(t, summary.Failures[0].Elapsed, time.Duration(0))
	require.Contains(t, summary.String(), "Failed executions: 1")
	require.Contains(t, summary.String(), "Failed execution details:")
	require.Contains(t, summary.String(), "workflow=workflow-4")
	require.Contains(t, summary.String(), "Peak in-flight executions:")
}

func TestBatchConfigValidation(t *testing.T) {
	tests := []struct {
		name   string
		config BatchConfig
		valid  bool
	}{
		{name: "defaults", config: BatchConfig{Count: defaultBatchCount, Concurrency: defaultBatchConcurrency, SLA: defaultBatchSLA}, valid: true},
		{name: "zero count", config: BatchConfig{Count: 0, Concurrency: 1, SLA: time.Second}},
		{name: "count too large", config: BatchConfig{Count: maximumBatchCount + 1, Concurrency: 1, SLA: time.Second}},
		{name: "zero concurrency", config: BatchConfig{Count: 1, Concurrency: 0, SLA: time.Second}},
		{name: "concurrency too large", config: BatchConfig{Count: maximumBatchConcurrency + 1, Concurrency: maximumBatchConcurrency + 1, SLA: time.Second}},
		{name: "concurrency exceeds count", config: BatchConfig{Count: 1, Concurrency: 2, SLA: time.Second}},
		{name: "zero SLA", config: BatchConfig{Count: 1, Concurrency: 1, SLA: 0}},
		{name: "SLA too long", config: BatchConfig{Count: 1, Concurrency: 1, SLA: maximumBatchSLA + time.Second}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := test.config.Validate()
			if test.valid {
				require.NoError(t, err)
			} else {
				require.Error(t, err)
			}
		})
	}
}
