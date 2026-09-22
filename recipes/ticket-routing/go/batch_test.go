package main

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
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
	require.Contains(t, tickets[0].TicketID, "synthetic-0001")
	require.Contains(t, tickets[1].TicketID, "synthetic-0002")
}

func TestBalancedLiveSampleCoversAllDepartmentsDeterministically(t *testing.T) {
	tickets, err := loadBatchTicketsWithSample(filepath.Join("..", "testdata", "tickets.jsonl"), 4, time.Second, liveSampleBalanced)
	require.NoError(t, err)
	require.Len(t, tickets, 4)

	expectedFixtureIDs := []string{"synthetic-0001", "synthetic-0011", "synthetic-0021", "synthetic-0031"}
	seenTicketIDs := make(map[string]struct{})
	for index, ticket := range tickets {
		require.Contains(t, ticket.TicketID, expectedFixtureIDs[index])
		_, duplicate := seenTicketIDs[ticket.TicketID]
		require.False(t, duplicate)
		seenTicketIDs[ticket.TicketID] = struct{}{}
	}

	jobs := newBatchJobs(tickets, "balanced-run")
	seenWorkflowIDs := make(map[string]struct{})
	for _, job := range jobs {
		_, duplicate := seenWorkflowIDs[job.WorkflowID]
		require.False(t, duplicate)
		seenWorkflowIDs[job.WorkflowID] = struct{}{}
	}
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

func TestRunBoundedBatchAggregatesJevMetricsAndMissingUsage(t *testing.T) {
	jobs := []batchJob{
		{WorkflowID: "workflow-1", Ticket: Ticket{TicketID: "ticket-1"}},
		{WorkflowID: "workflow-2", Ticket: Ticket{TicketID: "ticket-2"}},
		{WorkflowID: "workflow-3", Ticket: Ticket{TicketID: "ticket-3"}},
	}
	summary := runBoundedBatch(context.Background(), jobs, 2, func(_ context.Context, job batchJob) (TicketResult, error) {
		switch job.Ticket.TicketID {
		case "ticket-1":
			return TicketResult{
				Department: DepartmentBilling,
				Status:     StatusSLATimeout,
				Classification: RoutingDecision{
					InferenceLatency:   100 * time.Millisecond,
					InputTokens:        600,
					OutputTokens:       120,
					TokenUsageReported: true,
				},
			}, nil
		case "ticket-2":
			return TicketResult{
				Status: StatusUnroutable,
				Classification: RoutingDecision{
					InferenceLatency: 200 * time.Millisecond,
				},
			}, nil
		default:
			return TicketResult{}, fmt.Errorf("synthetic Cadence failure")
		}
	})

	require.Equal(t, 2, summary.Completed)
	require.Equal(t, 1, summary.Failed)
	require.Equal(t, 2, summary.JevResponses)
	require.Equal(t, 300*time.Millisecond, summary.JevInferenceTotal)
	require.Equal(t, 100*time.Millisecond, summary.JevInferenceMin)
	require.Equal(t, 200*time.Millisecond, summary.JevInferenceMax)
	require.Equal(t, 150*time.Millisecond, summary.averageJevInferenceLatency())
	require.Equal(t, int64(600), summary.InputTokens)
	require.Equal(t, int64(120), summary.OutputTokens)
	require.Equal(t, 1, summary.MissingTokenUsage)
	require.Len(t, summary.ExecutionTimings, 3)

	price := 0.042
	report := summary.LiveString(time.Second, &price)
	require.Contains(t, report, "Technically failed executions: 1")
	require.Contains(t, report, "Successful Jev HTTP inference latency")
	require.Contains(t, report, "Provider-reported input tokens: 600")
	require.Contains(t, report, "Successful responses missing complete token usage: 1")
	require.Contains(t, report, "Illustrative successful-response input cost: $0.00002520")
	require.Contains(t, report, "Per-ticket timing details:")
	require.Contains(t, report, "Token and cost totals cover successful recorded responses only")
}

func TestClassificationDetailsIncludesFieldsInSubmissionOrder(t *testing.T) {
	timings := []batchExecutionTiming{
		{
			SubmissionIndex:  2,
			TicketID:         "ticket-3",
			Outcome:          StatusUnroutable,
			InferenceLatency: 30 * time.Millisecond,
			Classification: RoutingDecision{
				Department:           "unknown",
				DepartmentConfidence: 0.40,
				Priority:             PriorityLow,
				PriorityConfidence:   0.70,
				Complexity:           ComplexityTier1,
				ComplexityConfidence: 0.80,
				Model:                "jev-test",
			},
		},
		{SubmissionIndex: 1, TicketID: "ticket-2", Outcome: "TECHNICAL_FAILURE"},
		{
			SubmissionIndex:  0,
			TicketID:         "ticket-1",
			Outcome:          StatusSLATimeout,
			InferenceLatency: 20 * time.Millisecond,
			Classification: RoutingDecision{
				Department:           DepartmentBilling,
				DepartmentConfidence: 0.99,
				Priority:             PriorityHigh,
				PriorityConfidence:   0.97,
				Complexity:           ComplexityTier2,
				ComplexityConfidence: 0.88,
				Model:                "jev-test",
			},
		},
	}

	report := classificationDetails(timings)
	require.Less(t, strings.Index(report, "ticket=ticket-1"), strings.Index(report, "ticket=ticket-2"))
	require.Less(t, strings.Index(report, "ticket=ticket-2"), strings.Index(report, "ticket=ticket-3"))
	require.Contains(t, report, "department=billing department-confidence=0.99")
	require.Contains(t, report, "priority=high priority-confidence=0.97")
	require.Contains(t, report, "complexity=tier2 complexity-confidence=0.88")
	require.Contains(t, report, "model=jev-test outcome=SLA_TIMEOUT jev-request-response-latency=20ms")
	require.Contains(t, report, "ticket=ticket-3 department=unknown")

	failureLine := "ticket=ticket-2 outcome=TECHNICAL_FAILURE"
	require.Contains(t, report, failureLine)
	require.NotContains(t, report[strings.Index(report, failureLine):strings.Index(report, "ticket=ticket-3")], "department=")
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

func TestLiveBatchConfigRequiresExplicitBoundedOptIn(t *testing.T) {
	price := 0.042
	valid := LiveBatchConfig{
		BatchConfig:               BatchConfig{Count: 3, Concurrency: 1, SLA: time.Second},
		Provider:                  "jev",
		CountExplicit:             true,
		ConcurrencyExplicit:       true,
		ConfirmLive:               true,
		TaskList:                  "ticket-routing-jev",
		TaskListExplicit:          true,
		Sample:                    liveSampleSequential,
		InputTokenPricePerMillion: &price,
	}
	require.NoError(t, valid.Validate())

	tests := []struct {
		name   string
		mutate func(*LiveBatchConfig)
	}{
		{name: "mock provider", mutate: func(config *LiveBatchConfig) { config.Provider = "mock" }},
		{name: "implicit count", mutate: func(config *LiveBatchConfig) { config.CountExplicit = false }},
		{name: "implicit concurrency", mutate: func(config *LiveBatchConfig) { config.ConcurrencyExplicit = false }},
		{name: "zero count", mutate: func(config *LiveBatchConfig) { config.Count = 0 }},
		{name: "count above limit", mutate: func(config *LiveBatchConfig) { config.Count = maximumLiveBatchCount + 1 }},
		{name: "zero concurrency", mutate: func(config *LiveBatchConfig) { config.Concurrency = 0 }},
		{name: "concurrency above limit", mutate: func(config *LiveBatchConfig) { config.Concurrency = maximumLiveConcurrency + 1 }},
		{name: "concurrency above count", mutate: func(config *LiveBatchConfig) {
			config.Count = 1
			config.Concurrency = 2
		}},
		{name: "confirmation missing", mutate: func(config *LiveBatchConfig) { config.ConfirmLive = false }},
		{name: "implicit task list", mutate: func(config *LiveBatchConfig) { config.TaskListExplicit = false }},
		{name: "shared task list", mutate: func(config *LiveBatchConfig) { config.TaskList = TaskList }},
		{name: "unsupported sample", mutate: func(config *LiveBatchConfig) { config.Sample = "random" }},
		{name: "negative price", mutate: func(config *LiveBatchConfig) { bad := -1.0; config.InputTokenPricePerMillion = &bad }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := valid
			test.mutate(&config)
			require.Error(t, config.Validate())
		})
	}
}

func TestRunLiveBatchRejectsBeforeWorkflowSubmission(t *testing.T) {
	config := LiveBatchConfig{
		BatchConfig:         BatchConfig{Count: 3, Concurrency: 1, SLA: time.Second},
		Provider:            "jev",
		CountExplicit:       true,
		ConcurrencyExplicit: true,
		ConfirmLive:         false,
		TaskList:            "ticket-routing-jev",
		TaskListExplicit:    true,
		Sample:              liveSampleSequential,
	}

	err := runLiveBatch(context.Background(), nil, config, nil)
	require.Error(t, err)
	require.Contains(t, err.Error(), "-confirm-live")
}
