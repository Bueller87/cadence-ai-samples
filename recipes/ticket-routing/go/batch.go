package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"go.uber.org/cadence/client"
)

const (
	defaultBatchCount       = 40
	defaultBatchConcurrency = 10
	defaultBatchSLA         = time.Second
	maximumBatchCount       = 10000
	maximumBatchConcurrency = 100
	maximumBatchSLA         = time.Minute
	batchDatasetPath        = "../testdata/tickets.jsonl"
	maximumDatasetLineSize  = 1 << 20
)

// BatchConfig limits a local functional batch demonstration.
type BatchConfig struct {
	Count       int
	Concurrency int
	SLA         time.Duration
}

func (config BatchConfig) Validate() error {
	if config.Count < 1 || config.Count > maximumBatchCount {
		return fmt.Errorf("batch count must be between 1 and %d", maximumBatchCount)
	}
	if config.Concurrency < 1 || config.Concurrency > maximumBatchConcurrency {
		return fmt.Errorf("batch concurrency must be between 1 and %d", maximumBatchConcurrency)
	}
	if config.Concurrency > config.Count {
		return fmt.Errorf("batch concurrency cannot exceed batch count")
	}
	if config.SLA <= 0 || config.SLA > maximumBatchSLA {
		return fmt.Errorf("batch SLA must be greater than zero and no more than %s", maximumBatchSLA)
	}
	return nil
}

type datasetTicket struct {
	TicketID           string     `json:"ticket_id"`
	Message            string     `json:"message"`
	ExpectedDepartment Department `json:"expected_department"`
	ExpectedPriority   Priority   `json:"expected_priority"`
	ExpectedComplexity Complexity `json:"expected_complexity"`
}

// loadBatchTickets validates the fixture labels but does not use them for
// routing or accuracy claims. They remain provisional test-fixture metadata.
func loadBatchTickets(path string, count int, sla time.Duration) ([]Ticket, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open batch dataset: %w", err)
	}
	defer file.Close()

	var records []datasetTicket
	seenIDs := make(map[string]struct{})
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), maximumDatasetLineSize)
	lineNumber := 0
	for scanner.Scan() {
		lineNumber++
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			return nil, fmt.Errorf("dataset line %d is blank", lineNumber)
		}
		var record datasetTicket
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return nil, fmt.Errorf("parse dataset line %d: %w", lineNumber, err)
		}
		if err := validateDatasetTicket(record, seenIDs); err != nil {
			return nil, fmt.Errorf("validate dataset line %d: %w", lineNumber, err)
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read batch dataset: %w", err)
	}
	if len(records) == 0 {
		return nil, fmt.Errorf("batch dataset has no records")
	}

	tickets := make([]Ticket, count)
	for index := range tickets {
		record := records[index%len(records)]
		tickets[index] = Ticket{
			TicketID: fmt.Sprintf("batch-%06d-%s", index+1, record.TicketID),
			Message:  record.Message,
			SLA: SLAConfig{
				Critical: sla,
				High:     sla,
				Normal:   sla,
				Low:      sla,
			},
		}
	}
	return tickets, nil
}

func validateDatasetTicket(record datasetTicket, seenIDs map[string]struct{}) error {
	record.TicketID = strings.TrimSpace(record.TicketID)
	if record.TicketID == "" {
		return fmt.Errorf("ticket ID is required")
	}
	if strings.TrimSpace(record.Message) == "" {
		return fmt.Errorf("message is required")
	}
	if _, exists := seenIDs[record.TicketID]; exists {
		return fmt.Errorf("duplicate ticket ID %q", record.TicketID)
	}
	if !validDepartment(record.ExpectedDepartment) || !validPriority(record.ExpectedPriority) || !validComplexity(record.ExpectedComplexity) {
		return fmt.Errorf("ticket %q has unsupported provisional labels", record.TicketID)
	}
	seenIDs[record.TicketID] = struct{}{}
	return nil
}

type batchJob struct {
	Ticket     Ticket
	WorkflowID string
}

func newBatchJobs(tickets []Ticket, runToken string) []batchJob {
	jobs := make([]batchJob, len(tickets))
	for index, ticket := range tickets {
		jobs[index] = batchJob{
			Ticket:     ticket,
			WorkflowID: fmt.Sprintf("ticket-routing-batch-%s-%s", runToken, ticket.TicketID),
		}
	}
	return jobs
}

type batchExecutor func(context.Context, batchJob) (TicketResult, error)

type batchSummary struct {
	TotalSubmitted         int
	Completed              int
	Failed                 int
	Unroutable             int
	Acknowledged           int
	SLATimeout             int
	ByDepartment           map[Department]int
	Duration               time.Duration
	PeakInFlight           int
	ExecutionDurationTotal time.Duration
	ExecutionDurationMin   time.Duration
	ExecutionDurationMax   time.Duration
	Failures               []batchFailure
}

type batchFailure struct {
	WorkflowID string
	TicketID   string
	StartedAt  time.Time
	Elapsed    time.Duration
	Error      string
}

func runBoundedBatch(ctx context.Context, jobs []batchJob, concurrency int, execute batchExecutor) batchSummary {
	started := time.Now()
	summary := batchSummary{TotalSubmitted: len(jobs), ByDepartment: make(map[Department]int)}
	jobChannel := make(chan batchJob)
	resultChannel := make(chan batchOutcome, concurrency)
	var inFlight atomic.Int32
	var peakInFlight atomic.Int32

	var workers sync.WaitGroup
	for index := 0; index < concurrency; index++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for job := range jobChannel {
				startedAt := time.Now()
				current := inFlight.Add(1)
				for {
					peak := peakInFlight.Load()
					if current <= peak || peakInFlight.CompareAndSwap(peak, current) {
						break
					}
				}
				result, err := execute(ctx, job)
				inFlight.Add(-1)
				resultChannel <- batchOutcome{job: job, result: result, err: err, startedAt: startedAt, elapsed: time.Since(startedAt)}
			}
		}()
	}

	go func() {
		for _, job := range jobs {
			jobChannel <- job
		}
		close(jobChannel)
	}()

	for range jobs {
		outcome := <-resultChannel
		summary.addExecutionDuration(outcome.elapsed)
		if outcome.err != nil {
			summary.Failed++
			summary.Failures = append(summary.Failures, batchFailure{
				WorkflowID: outcome.job.WorkflowID,
				TicketID:   outcome.job.Ticket.TicketID,
				StartedAt:  outcome.startedAt,
				Elapsed:    outcome.elapsed,
				Error:      fmt.Sprintf("%T: %v", outcome.err, outcome.err),
			})
			continue
		}
		summary.Completed++
		summary.addResult(outcome.result)
	}
	workers.Wait()
	summary.Duration = time.Since(started)
	summary.PeakInFlight = int(peakInFlight.Load())
	return summary
}

type batchOutcome struct {
	job       batchJob
	result    TicketResult
	err       error
	startedAt time.Time
	elapsed   time.Duration
}

func (summary *batchSummary) addExecutionDuration(duration time.Duration) {
	summary.ExecutionDurationTotal += duration
	if summary.ExecutionDurationMin == 0 || duration < summary.ExecutionDurationMin {
		summary.ExecutionDurationMin = duration
	}
	if duration > summary.ExecutionDurationMax {
		summary.ExecutionDurationMax = duration
	}
}

func (summary batchSummary) averageExecutionDuration() time.Duration {
	if summary.TotalSubmitted == 0 {
		return 0
	}
	return summary.ExecutionDurationTotal / time.Duration(summary.TotalSubmitted)
}

func (summary *batchSummary) addResult(result TicketResult) {
	switch result.Status {
	case StatusUnroutable:
		summary.Unroutable++
	case StatusAcknowledged:
		summary.Acknowledged++
	case StatusSLATimeout:
		summary.SLATimeout++
	}
	if validDepartment(result.Department) {
		summary.ByDepartment[result.Department]++
	}
}

func (summary batchSummary) completedPerSecond() float64 {
	if summary.Duration <= 0 {
		return 0
	}
	return float64(summary.Completed) / summary.Duration.Seconds()
}

func runBatch(ctx context.Context, cadenceClient client.Client, taskList string, config BatchConfig, output io.Writer) error {
	tickets, err := loadBatchTickets(batchDatasetPath, config.Count, config.SLA)
	if err != nil {
		return err
	}
	runToken := time.Now().UTC().Format("20060102T150405.000000000")
	jobs := newBatchJobs(tickets, runToken)
	if _, err := fmt.Fprintf(output, "Starting mock batch: count=%d concurrency=%d batch-sla=%s\n", config.Count, config.Concurrency, config.SLA); err != nil {
		return fmt.Errorf("write batch start: %w", err)
	}

	summary := runBoundedBatch(ctx, jobs, config.Concurrency, func(ctx context.Context, job batchJob) (TicketResult, error) {
		run, err := cadenceClient.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
			ID:                              job.WorkflowID,
			TaskList:                        taskList,
			ExecutionStartToCloseTimeout:    parentWorkflowExecutionTimeout(job.Ticket.SLA),
			DecisionTaskStartToCloseTimeout: 10 * time.Second,
		}, TicketIntakeWorkflow, job.Ticket)
		if err != nil {
			return TicketResult{}, fmt.Errorf("start ticket %s: %w", job.Ticket.TicketID, err)
		}
		var result TicketResult
		if err := run.Get(ctx, &result); err != nil {
			return TicketResult{}, fmt.Errorf("wait for ticket %s: %w", job.Ticket.TicketID, err)
		}
		return result, nil
	})
	if _, err := fmt.Fprint(output, summary.String()); err != nil {
		return fmt.Errorf("write batch summary: %w", err)
	}
	return nil
}

func (summary batchSummary) String() string {
	return fmt.Sprintf("\nBatch summary\nTotal submitted: %d\nCompleted executions: %d\nFailed executions: %d\nUNROUTABLE: %d\nACKNOWLEDGED: %d\nSLA_TIMEOUT: %d\nResults by department: billing=%d technical=%d account=%d content=%d\nPeak in-flight executions: %d\nBatch wall-clock duration: %s\nPer-execution client wait: min=%s average=%s max=%s\nAverage completed workflows per second: %.2f\n%s",
		summary.TotalSubmitted,
		summary.Completed,
		summary.Failed,
		summary.Unroutable,
		summary.Acknowledged,
		summary.SLATimeout,
		summary.ByDepartment[DepartmentBilling],
		summary.ByDepartment[DepartmentTechnical],
		summary.ByDepartment[DepartmentAccount],
		summary.ByDepartment[DepartmentContent],
		summary.PeakInFlight,
		summary.Duration.Round(time.Millisecond),
		summary.ExecutionDurationMin.Round(time.Millisecond),
		summary.averageExecutionDuration().Round(time.Millisecond),
		summary.ExecutionDurationMax.Round(time.Millisecond),
		summary.completedPerSecond(),
		failureSummary(summary.Failures),
	)
}

func failureSummary(failures []batchFailure) string {
	if len(failures) == 0 {
		return ""
	}
	var lines []string
	for _, failure := range failures {
		lines = append(lines, fmt.Sprintf("workflow=%s ticket=%s started=%s elapsed=%s error=%s",
			failure.WorkflowID,
			failure.TicketID,
			failure.StartedAt.UTC().Format(time.RFC3339Nano),
			failure.Elapsed.Round(time.Millisecond),
			failure.Error,
		))
	}
	return "Failed execution details:\n- " + strings.Join(lines, "\n- ") + "\n"
}
