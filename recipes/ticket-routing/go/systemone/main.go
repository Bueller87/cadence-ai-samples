package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math/big"
	"net/http"
	"os"
	"reflect"
	"runtime"
	"strings"
	"time"

	"go.uber.org/cadence"
	"go.uber.org/cadence/.gen/go/cadence/workflowserviceclient"
	"go.uber.org/cadence/activity"
	"go.uber.org/cadence/client"
	"go.uber.org/cadence/worker"
	"go.uber.org/yarpc"
	"go.uber.org/yarpc/transport/tchannel"
)

const (
	cadenceServiceName     = "cadence-frontend"
	defaultAddress         = "127.0.0.1:7933"
	defaultDomain          = "cadence-ai-samples"
	defaultWorkflowID      = "ticket-routing-demo"
	defaultDatasetPath     = "../../testdata/tickets.jsonl"
	assignmentPollInterval = 250 * time.Millisecond
	assignmentPollTimeout  = 2*time.Minute + 15*time.Second
)

type Config struct {
	Action       string
	Mode         string
	Address      string
	Domain       string
	TaskList     string
	WorkflowID   string
	Text         string
	ClassifierID string
	CatalogDir   string
	ConfirmLive  bool
}

func (config Config) Validate() error {
	switch config.Action {
	case "worker", "start":
	default:
		return fmt.Errorf("unsupported action %q; use worker or start", config.Action)
	}
	switch config.Mode {
	case "mock", "live":
	default:
		return fmt.Errorf("unsupported mode %q; use mock or live", config.Mode)
	}
	for name, value := range map[string]string{
		"address": config.Address, "domain": config.Domain, "task list": config.TaskList,
	} {
		if strings.TrimSpace(value) == "" {
			return fmt.Errorf("%s is required", name)
		}
	}
	if config.Action == "worker" && config.Mode == "live" {
		if strings.TrimSpace(config.ClassifierID) == "" {
			return errors.New("live worker requires -classifier-id")
		}
		if strings.TrimSpace(config.CatalogDir) == "" {
			return errors.New("live worker requires -catalog-dir")
		}
		if !config.ConfirmLive {
			return errors.New("live worker requires -confirm-live")
		}
	}
	if config.Action == "start" {
		if strings.TrimSpace(config.WorkflowID) == "" {
			return errors.New("workflow ID is required")
		}
	}
	return nil
}

func parseCommand(args []string) (Config, error) {
	if len(args) == 0 {
		return Config{}, errors.New("action is required; use worker or start")
	}
	config := Config{Action: args[0]}
	flags := flag.NewFlagSet(config.Action, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	flags.StringVar(&config.Mode, "mode", "", "execution mode: mock or live (required)")
	flags.StringVar(&config.Address, "address", defaultAddress, "Cadence frontend address")
	flags.StringVar(&config.Domain, "domain", defaultDomain, "Cadence domain")
	flags.StringVar(&config.TaskList, "task-list", TaskList, "Cadence task list")
	switch config.Action {
	case "worker":
		flags.StringVar(&config.ClassifierID, "classifier-id", "laya-local", "classifiers.yaml ID for live mode")
		flags.StringVar(&config.CatalogDir, "catalog-dir", defaultCatalogDir, "directory containing classifiers.yaml")
		flags.BoolVar(&config.ConfirmLive, "confirm-live", false, "confirm live classifier API usage")
	case "start":
		flags.StringVar(&config.WorkflowID, "workflow-id", defaultWorkflowID, "stable Workflow ID")
		flags.StringVar(&config.Text, "text", "", "optional ticket text; omit to select a bundled fixture")
	}
	if err := flags.Parse(args[1:]); err != nil {
		return Config{}, err
	}
	if flags.NArg() != 0 {
		return Config{}, fmt.Errorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	if err := config.Validate(); err != nil {
		return Config{}, err
	}
	return config, nil
}

func main() {
	config, err := parseCommand(os.Args[1:])
	if err != nil {
		log.Fatal(err)
	}

	var selectedClassifier string
	var classifierActivity interface{}
	if config.Action == "worker" {
		selectedClassifier, classifierActivity, err = configuredClassifier(
			config.Mode, config.ClassifierID, config.CatalogDir,
		)
		if err != nil {
			log.Fatal(err)
		}
	}

	service, closeDispatcher, err := newServiceClient(config.Address)
	if err != nil {
		log.Fatal(err)
	}
	defer closeDispatcher()

	switch config.Action {
	case "worker":
		if err := runWorker(service, config.Domain, config.TaskList, selectedClassifier, classifierActivity); err != nil {
			log.Fatal(err)
		}
	case "start":
		ticket, err := ticketForStart(config.Text, defaultDatasetPath, randomIndex)
		if err != nil {
			log.Fatal(err)
		}
		if err := start(context.Background(), client.NewClient(service, config.Domain, nil), config.TaskList, config.WorkflowID, ticket, os.Stdout); err != nil {
			log.Fatal(err)
		}
	}
}

func runWorker(service workflowserviceclient.Interface, domain, taskList, classifierID string, classifierActivity interface{}) error {
	w, err := worker.NewV2(service, domain, taskList, worker.Options{})
	if err != nil {
		return fmt.Errorf("create worker: %w", err)
	}
	w.RegisterWorkflow(TicketIntakeWorkflow)
	w.RegisterWorkflow(BillingWorkflow)
	w.RegisterWorkflow(TechnicalWorkflow)
	w.RegisterWorkflow(AccountWorkflow)
	w.RegisterWorkflow(ContentWorkflow)
	w.RegisterActivityWithOptions(classifierActivity, activity.RegisterOptions{Name: classificationActivityName()})
	log.Printf("ticket-routing worker polling domain=%s task-list=%s classifier-id=%s", domain, taskList, classifierID)
	return w.Run()
}

func start(ctx context.Context, cadenceClient client.Client, taskList, workflowID string, ticket Ticket, output io.Writer) error {
	waiter := func(ctx context.Context, workflowID, runID string) (ParentAssignmentStatus, error) {
		return waitForParentAssignment(ctx, cadenceClient, workflowID, runID, assignmentPollInterval, assignmentPollTimeout)
	}
	return startWithWaiter(ctx, cadenceClient, taskList, workflowID, ticket, output, waiter)
}

type assignmentWaiter func(context.Context, string, string) (ParentAssignmentStatus, error)

func startWithWaiter(ctx context.Context, cadenceClient client.Client, taskList, workflowID string, ticket Ticket, output io.Writer, waiter assignmentWaiter) error {
	fmt.Fprintf(output, "selected ticket ID: %s\nselected ticket message: %s\n", ticket.TicketID, ticket.Message)
	run, err := cadenceClient.ExecuteWorkflow(ctx, startOptions(taskList, workflowID), TicketIntakeWorkflow, ticket)
	if err != nil {
		if cadence.IsWorkflowExecutionAlreadyStartedError(err) {
			return fmt.Errorf(
				"workflow-id=%s already has an open Run; wait for it to close or pass a different -workflow-id",
				workflowID,
			)
		}
		return fmt.Errorf("start workflow-id=%s: %w", workflowID, err)
	}
	fmt.Fprintf(output, "started parent workflow ID: %s\nparent run ID: %s\n", run.GetID(), run.GetRunID())

	type assignmentOutcome struct {
		status ParentAssignmentStatus
		err    error
	}
	type terminalOutcome struct {
		result TicketResult
		err    error
	}

	pollCtx, cancelPolling := context.WithCancel(ctx)
	defer cancelPolling()
	assignmentCh := make(chan assignmentOutcome, 1)
	terminalCh := make(chan terminalOutcome, 1)
	go func() {
		status, err := waiter(pollCtx, run.GetID(), run.GetRunID())
		assignmentCh <- assignmentOutcome{status: status, err: err}
	}()
	go func() {
		var result TicketResult
		err := run.Get(ctx, &result)
		terminalCh <- terminalOutcome{result: result, err: err}
	}()

	select {
	case terminal := <-terminalCh:
		cancelPolling()
		if terminal.err != nil {
			return fmt.Errorf("wait for workflow-id=%s: %w", workflowID, terminal.err)
		}
		printAssignmentStatus(output, parentAssignmentStatusFromResult(terminal.result))
		fmt.Fprint(output, ticketResultSummary(terminal.result))
		return nil
	case assignment := <-assignmentCh:
		if assignment.err != nil {
			fmt.Fprintf(output, "assignment status unavailable before terminal wait: %v\n", assignment.err)
		} else {
			printAssignmentStatus(output, assignment.status)
		}
		fmt.Fprintln(output, "waiting for workflow result...")
		terminal := <-terminalCh
		if terminal.err != nil {
			return fmt.Errorf("wait for workflow-id=%s: %w", workflowID, terminal.err)
		}
		fmt.Fprint(output, ticketResultSummary(terminal.result))
		return nil
	}
}

func printAssignmentStatus(output io.Writer, status ParentAssignmentStatus) {
	if status.Status == StatusAwaitingAcknowledgment || status.Status == StatusAcknowledged || status.Status == StatusSLATimeout {
		fmt.Fprintf(output, "classified assignment\nchild workflow ID: %s\nemployee ID: %s\nassignment ID: %s\nsignal name: %s\nacknowledgment SLA: %s\n",
			status.ChildWorkflowID, status.EmployeeID, status.AssignmentID, status.SignalName, status.SLADuration)
	} else if status.Status == StatusUnroutable {
		fmt.Fprintf(output, "classification status: %s\nreason: %s\n", status.Status, status.Reason)
	}
}

func parentAssignmentStatusFromResult(result TicketResult) ParentAssignmentStatus {
	return ParentAssignmentStatus{
		Status:          result.Status,
		TicketID:        result.TicketID,
		Department:      result.Department,
		EmployeeID:      result.EmployeeID,
		AssignmentID:    result.AssignmentID,
		ChildWorkflowID: result.ChildWorkflowID,
		SignalName:      AcknowledgmentSignalName,
		SLADuration:     result.SLADuration,
		Reason:          result.Reason,
	}
}

func startOptions(taskList, workflowID string) client.StartWorkflowOptions {
	return client.StartWorkflowOptions{
		ID:                              workflowID,
		TaskList:                        taskList,
		ExecutionStartToCloseTimeout:    parentWorkflowExecutionTimeout(SLAConfig{}),
		DecisionTaskStartToCloseTimeout: 10 * time.Second,
		WorkflowIDReusePolicy:           client.WorkflowIDReusePolicyAllowDuplicate,
	}
}

func ticketForStart(text, datasetPath string, choose func(int) (int, error)) (Ticket, error) {
	text = strings.TrimSpace(text)
	if text != "" {
		hash := sha256.Sum256([]byte(text))
		return Ticket{TicketID: fmt.Sprintf("custom-%x", hash[:6]), Message: text}, nil
	}
	tickets, err := loadSyntheticTickets(datasetPath)
	if err != nil {
		return Ticket{}, err
	}
	index, err := choose(len(tickets))
	if err != nil {
		return Ticket{}, fmt.Errorf("select synthetic ticket: %w", err)
	}
	if index < 0 || index >= len(tickets) {
		return Ticket{}, fmt.Errorf("selected synthetic ticket index %d is out of range", index)
	}
	return tickets[index], nil
}

func loadSyntheticTickets(path string) ([]Ticket, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open synthetic ticket dataset: %w", err)
	}
	defer file.Close()

	var tickets []Ticket
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var ticket Ticket
		if err := json.Unmarshal(scanner.Bytes(), &ticket); err != nil {
			return nil, fmt.Errorf("parse synthetic ticket dataset line %d: %w", len(tickets)+1, err)
		}
		if strings.TrimSpace(ticket.TicketID) == "" || strings.TrimSpace(ticket.Message) == "" {
			return nil, fmt.Errorf("synthetic ticket dataset line %d requires ticket_id and message", len(tickets)+1)
		}
		ticket.SLA = SLAConfig{}
		tickets = append(tickets, ticket)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read synthetic ticket dataset: %w", err)
	}
	if len(tickets) == 0 {
		return nil, errors.New("synthetic ticket dataset is empty")
	}
	return tickets, nil
}

func randomIndex(length int) (int, error) {
	if length <= 0 {
		return 0, errors.New("selection length must be positive")
	}
	value, err := rand.Int(rand.Reader, big.NewInt(int64(length)))
	if err != nil {
		return 0, err
	}
	return int(value.Int64()), nil
}

func waitForParentAssignment(ctx context.Context, cadenceClient client.Client, workflowID, runID string, interval, timeout time.Duration) (ParentAssignmentStatus, error) {
	query := func(queryCtx context.Context) (ParentAssignmentStatus, error) {
		value, err := cadenceClient.QueryWorkflow(queryCtx, workflowID, runID, ParentAssignmentQueryName)
		if err != nil {
			return ParentAssignmentStatus{}, err
		}
		var status ParentAssignmentStatus
		if err := value.Get(&status); err != nil {
			return ParentAssignmentStatus{}, fmt.Errorf("decode parent assignment status: %w", err)
		}
		return status, nil
	}
	return waitForParentAssignmentWithQuery(ctx, query, interval, timeout)
}

type parentAssignmentQuery func(context.Context) (ParentAssignmentStatus, error)

func waitForParentAssignmentWithQuery(ctx context.Context, query parentAssignmentQuery, interval, timeout time.Duration) (ParentAssignmentStatus, error) {
	waitCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	var lastErr error
	for {
		status, err := query(waitCtx)
		if err == nil {
			if status.Status != "CLASSIFICATION_PENDING" {
				return status, nil
			}
		} else {
			lastErr = err
		}
		select {
		case <-waitCtx.Done():
			if lastErr != nil {
				return ParentAssignmentStatus{}, fmt.Errorf("poll parent assignment status: %w", lastErr)
			}
			return ParentAssignmentStatus{}, fmt.Errorf("poll parent assignment status: %w", waitCtx.Err())
		case <-ticker.C:
		}
	}
}

func ticketResultSummary(result TicketResult) string {
	department := string(result.Department)
	employeeID := result.EmployeeID
	if department == "" {
		department = "N/A"
	}
	if employeeID == "" {
		employeeID = "N/A"
	}
	return fmt.Sprintf(
		"Workflow status: Completed\nTicket ID: %s\nDepartment: %s\nPriority: %s\nComplexity: %s\nAssigned employee: %s\nAssignment ID: %s\nBusiness status: %s\nSLA met: %s\n",
		result.TicketID, department, result.Classification.Priority, result.Classification.Complexity,
		employeeID, result.AssignmentID, result.Status, slaMetLabel(result.Status, result.SLAMet),
	)
}

// configuredClassifier returns the selected classifier and Worker Activity.
// Mock mode returns before reading catalogs or credentials.
func configuredClassifier(mode, classifierID, catalogDir string) (string, interface{}, error) {
	if mode == "mock" {
		return mockClassifierID, ClassifyTicket, nil
	}
	if mode != "live" {
		return "", nil, fmt.Errorf("unsupported mode %q; use mock or live", mode)
	}

	classifierID = strings.TrimSpace(classifierID)
	config, err := loadClassifier(catalogDir, classifierID)
	if err != nil {
		return "", nil, err
	}
	apiKey := ""
	if config.Provider == classifierProviderTypeSafe {
		apiKey = strings.TrimSpace(os.Getenv("CLASSIFIER_AI_KEY"))
		if apiKey == "" {
			return "", nil, fmt.Errorf("classifier %q requires CLASSIFIER_AI_KEY", classifierID)
		}
	}
	classifier, err := newSystemOneClassifier(
		apiKey,
		config.Endpoint,
		config.Model,
		config.Provider,
		&http.Client{Timeout: 20 * time.Second},
	)
	if err != nil {
		return "", nil, err
	}
	return classifierID, classifier.ClassifyTicket, nil
}

func classificationActivityName() string {
	return runtime.FuncForPC(reflect.ValueOf(ClassifyTicket).Pointer()).Name()
}

func newServiceClient(address string) (workflowserviceclient.Interface, func(), error) {
	transport, err := tchannel.NewChannelTransport(tchannel.ServiceName("ticket-routing-sample"))
	if err != nil {
		return nil, nil, fmt.Errorf("create tchannel transport: %w", err)
	}
	dispatcher := yarpc.NewDispatcher(yarpc.Config{
		Name: "ticket-routing-sample",
		Outbounds: yarpc.Outbounds{
			cadenceServiceName: {Unary: transport.NewSingleOutbound(address)},
		},
	})
	if err := dispatcher.Start(); err != nil {
		return nil, nil, fmt.Errorf("start dispatcher: %w", err)
	}
	closeDispatcher := func() {
		if err := dispatcher.Stop(); err != nil {
			log.Printf("stop dispatcher: %v", err)
		}
	}
	return workflowserviceclient.New(dispatcher.ClientConfig(cadenceServiceName)), closeDispatcher, nil
}
