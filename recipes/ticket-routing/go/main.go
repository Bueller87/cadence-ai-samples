package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"reflect"
	"runtime"
	"strings"
	"time"

	"go.uber.org/cadence/.gen/go/cadence/workflowserviceclient"
	"go.uber.org/cadence/activity"
	"go.uber.org/cadence/client"
	"go.uber.org/cadence/worker"
	"go.uber.org/yarpc"
	"go.uber.org/yarpc/transport/tchannel"
)

const (
	cadenceServiceName      = "cadence-frontend"
	manualDemoTicketID      = "manual-billing-001"
	manualDemoTicketMessage = "I was charged twice for my StreamWave subscription."
	defaultManualSLA        = 2 * time.Minute
)

func main() {
	defaultSLA := defaultSLAConfig()
	mode := flag.String("mode", "demo", "run mode: worker, demo, start-ticket, acknowledge, live-demo, batch, or live-batch")
	address := flag.String("address", "127.0.0.1:7933", "Cadence frontend address")
	domain := flag.String("domain", "cadence-ai-samples", "Cadence domain")
	taskList := flag.String("task-list", TaskList, "Cadence task list")
	ticketID := flag.String("ticket-id", manualDemoTicketID, "ticket ID for start-ticket or acknowledge")
	department := flag.String("department", string(DepartmentBilling), "department for acknowledge")
	employeeID := flag.String("employee-id", BillingEmployeeID, "employee ID for acknowledge")
	assignmentID := flag.String("assignment-id", "", "assignment ID for acknowledge; derived from the other identifiers when omitted")
	manualSLA := flag.Duration("manual-sla", defaultManualSLA, "acknowledgment SLA for start-ticket")
	criticalSLA := flag.Duration("sla-critical", defaultSLA.Critical, "critical-priority acknowledgment SLA")
	highSLA := flag.Duration("sla-high", defaultSLA.High, "high-priority acknowledgment SLA")
	normalSLA := flag.Duration("sla-normal", defaultSLA.Normal, "normal-priority acknowledgment SLA")
	lowSLA := flag.Duration("sla-low", defaultSLA.Low, "low-priority acknowledgment SLA")
	batchCount := flag.Int("count", defaultBatchCount, "number of synthetic tickets to execute in batch mode")
	batchConcurrency := flag.Int("concurrency", defaultBatchConcurrency, "maximum in-flight workflow executions in batch mode")
	batchSLA := flag.Duration("batch-sla", defaultBatchSLA, "acknowledgment SLA for every routed ticket in batch mode")
	confirmLive := flag.Bool("confirm-live", false, "confirm that live-batch may consume real Jev API usage")
	liveSample := flag.String("sample", liveSampleSequential, "live-batch dataset selection: sequential or balanced")
	showClassifications := flag.Bool("show-classifications", false, "print ordered per-ticket classifications in live-batch mode")
	inputTokenPrice := flag.Float64("input-token-price-per-million", 0, "optional input-token price per million tokens for illustrative live-batch cost")
	flag.Parse()
	explicitFlags := make(map[string]bool)
	flag.Visit(func(value *flag.Flag) {
		explicitFlags[value.Name] = true
	})
	slaConfig := SLAConfig{Critical: *criticalSLA, High: *highSLA, Normal: *normalSLA, Low: *lowSLA}
	batchConfig := BatchConfig{Count: *batchCount, Concurrency: *batchConcurrency, SLA: *batchSLA}
	providerSetting := configuredProvider()
	var inputTokenPricePointer *float64
	if explicitFlags["input-token-price-per-million"] {
		inputTokenPricePointer = inputTokenPrice
	}
	liveBatchConfig := LiveBatchConfig{
		BatchConfig:               batchConfig,
		Provider:                  providerSetting,
		CountExplicit:             explicitFlags["count"],
		ConcurrencyExplicit:       explicitFlags["concurrency"],
		ConfirmLive:               *confirmLive,
		TaskList:                  *taskList,
		TaskListExplicit:          explicitFlags["task-list"],
		Sample:                    *liveSample,
		ShowClassifications:       *showClassifications,
		InputTokenPricePerMillion: inputTokenPricePointer,
	}
	if *mode == "batch" {
		if err := batchConfig.Validate(); err != nil {
			log.Fatal(err)
		}
	}
	if *mode == "live-batch" {
		fmt.Printf("Requested live Jev batch: count=%d concurrency=%d sample=%s task-list=%s\n", *batchCount, *batchConcurrency, *liveSample, *taskList)
		if err := liveBatchConfig.Validate(); err != nil {
			log.Fatal(err)
		}
	}

	provider, classifierActivity, err := configuredClassifier(*mode)
	if err != nil {
		log.Fatal(err)
	}
	if err := validateLiveTaskList(*mode, provider, *taskList, explicitFlags["task-list"]); err != nil {
		log.Fatal(err)
	}

	service, closeDispatcher, err := newServiceClient(*address)
	if err != nil {
		log.Fatal(err)
	}
	defer closeDispatcher()

	switch *mode {
	case "worker":
		if err := runWorker(service, *domain, *taskList, provider, classifierActivity); err != nil {
			log.Fatal(err)
		}
	case "demo":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := runDemo(context.Background(), cadenceClient, *taskList, slaConfig); err != nil {
			log.Fatal(err)
		}
	case "start-ticket":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := startManualTicket(context.Background(), cadenceClient, *taskList, *ticketID, *manualSLA, os.Stdout); err != nil {
			log.Fatal(err)
		}
	case "acknowledge":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := acknowledgeManualTicket(context.Background(), cadenceClient, *ticketID, Department(*department), *employeeID, *assignmentID, os.Stdout); err != nil {
			log.Fatal(err)
		}
	case "live-demo":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := runLiveDemo(context.Background(), cadenceClient, *taskList, slaConfig); err != nil {
			log.Fatal(err)
		}
	case "batch":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := runBatch(context.Background(), cadenceClient, *taskList, batchConfig, os.Stdout); err != nil {
			log.Fatal(err)
		}
	case "live-batch":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := runLiveBatch(context.Background(), cadenceClient, liveBatchConfig, os.Stdout); err != nil {
			log.Fatal(err)
		}
	default:
		fmt.Fprintf(os.Stderr, "unsupported mode %q; use worker, demo, start-ticket, acknowledge, live-demo, batch, or live-batch\n", *mode)
		os.Exit(2)
	}
}

func runWorker(service workflowserviceclient.Interface, domain, taskList, provider string, classifierActivity interface{}) error {
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

	log.Printf("ticket-routing worker polling domain=%s task-list=%s AI_PROVIDER=%s", domain, taskList, provider)
	return w.Run()
}

func runDemo(ctx context.Context, cadenceClient client.Client, taskList string, slaConfig SLAConfig) error {
	tickets := []Ticket{
		{TicketID: "demo-billing-001", Message: "I was charged twice for my monthly subscription.", SLA: slaConfig},
		{TicketID: "demo-technical-002", Message: "The app keeps crashing on multiple devices.", SLA: slaConfig},
		{TicketID: "demo-account-003", Message: "I am locked out and cannot reset my password.", SLA: slaConfig},
		{TicketID: "demo-content-004", Message: "The subtitles are missing from the newest episode.", SLA: slaConfig},
	}

	return runTickets(ctx, cadenceClient, taskList, "mock", tickets)
}

func runLiveDemo(ctx context.Context, cadenceClient client.Client, taskList string, slaConfig SLAConfig) error {
	ticketID := "live-jev-" + time.Now().UTC().Format("20060102T150405.000000000")
	tickets := []Ticket{{
		TicketID: ticketID,
		Message:  "I was charged twice for my StreamWave subscription and need help before my next billing date.",
		SLA:      slaConfig,
	}}
	return runTickets(ctx, cadenceClient, taskList, "real Jev", tickets)
}

func startManualTicket(ctx context.Context, cadenceClient client.Client, taskList, ticketID string, sla time.Duration, output io.Writer) error {
	ticketID = strings.TrimSpace(ticketID)
	if ticketID == "" {
		return fmt.Errorf("ticket ID is required")
	}
	if sla <= 0 {
		return fmt.Errorf("manual SLA must be greater than zero")
	}

	department := DepartmentBilling
	employeeID := BillingEmployeeID
	parentID := parentWorkflowID(ticketID)
	childID := childWorkflowID(parentID, department)
	assignmentID := assignmentIdentifier(ticketID, department, employeeID)
	ticket := Ticket{
		TicketID: ticketID,
		Message:  manualDemoTicketMessage,
		SLA:      SLAConfig{High: sla},
	}

	run, err := cadenceClient.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID:                              parentID,
		TaskList:                        taskList,
		ExecutionStartToCloseTimeout:    parentWorkflowExecutionTimeout(ticket.SLA),
		DecisionTaskStartToCloseTimeout: 10 * time.Second,
	}, TicketIntakeWorkflow, ticket)
	if err != nil {
		return fmt.Errorf("start manual ticket %s: %w", ticketID, err)
	}

	_, err = fmt.Fprintf(output, "ticket started\nparent workflow ID: %s\nparent run ID: %s\nchild workflow ID: %s\nticket ID: %s\ndepartment: %s\nemployee ID: %s\nassignment ID: %s\nsignal name: %s\nacknowledgment SLA: %s\n\nAcknowledge before the deadline:\ngo run . -mode acknowledge -ticket-id %q -department %q -employee-id %q -assignment-id %q\n\nwaiting for workflow result...\n",
		parentID, run.GetRunID(), childID, ticketID, department, employeeID, assignmentID, AcknowledgmentSignalName, sla,
		ticketID, department, employeeID, assignmentID)
	if err != nil {
		return fmt.Errorf("write manual ticket details: %w", err)
	}

	var result TicketResult
	if err := run.Get(ctx, &result); err != nil {
		return fmt.Errorf("wait for manual ticket %s: %w", ticketID, err)
	}
	if _, err := fmt.Fprint(output, ticketResultSummary(result)); err != nil {
		return fmt.Errorf("write manual ticket result: %w", err)
	}
	return nil
}

func acknowledgeManualTicket(ctx context.Context, cadenceClient client.Client, ticketID string, department Department, employeeID, assignmentID string, output io.Writer) error {
	ticketID = strings.TrimSpace(ticketID)
	employeeID = strings.TrimSpace(employeeID)
	assignmentID = strings.TrimSpace(assignmentID)
	if ticketID == "" || !validDepartment(department) || employeeID == "" {
		return fmt.Errorf("ticket ID, valid department, and employee ID are required")
	}
	if assignmentID == "" {
		assignmentID = assignmentIdentifier(ticketID, department, employeeID)
	}

	childID := childWorkflowID(parentWorkflowID(ticketID), department)
	payload := AcknowledgmentSignal{
		TicketID:     ticketID,
		EmployeeID:   employeeID,
		AssignmentID: assignmentID,
	}
	if err := cadenceClient.SignalWorkflow(ctx, childID, "", AcknowledgmentSignalName, payload); err != nil {
		return fmt.Errorf("signal child workflow %s: %w", childID, err)
	}

	if _, err := fmt.Fprintf(output, "acknowledgment sent\nchild workflow ID: %s\nticket ID: %s\nemployee ID: %s\nassignment ID: %s\n", childID, ticketID, employeeID, assignmentID); err != nil {
		return fmt.Errorf("write acknowledgment details: %w", err)
	}
	return nil
}

func runTickets(ctx context.Context, cadenceClient client.Client, taskList, classificationSource string, tickets []Ticket) error {
	for _, ticket := range tickets {
		log.Printf("incoming request: ticket=%s message=%q", ticket.TicketID, ticket.Message)

		run, err := cadenceClient.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
			ID:                              parentWorkflowID(ticket.TicketID),
			TaskList:                        taskList,
			ExecutionStartToCloseTimeout:    parentWorkflowExecutionTimeout(ticket.SLA),
			DecisionTaskStartToCloseTimeout: 10 * time.Second,
		}, TicketIntakeWorkflow, ticket)
		if err != nil {
			return fmt.Errorf("start ticket %s: %w", ticket.TicketID, err)
		}

		var result TicketResult
		if err := run.Get(ctx, &result); err != nil {
			return fmt.Errorf("wait for ticket %s: %w", ticket.TicketID, err)
		}

		log.Printf("%s classification: department=%s priority=%s complexity=%s model=%s", classificationSource, result.Classification.Department, result.Classification.Priority, result.Classification.Complexity, result.Classification.Model)
		log.Printf("classification confidence: department=%.3f priority=%.3f complexity=%.3f", result.Classification.DepartmentConfidence, result.Classification.PriorityConfidence, result.Classification.ComplexityConfidence)
		if result.Classification.InputTokens > 0 || result.Classification.OutputTokens > 0 {
			log.Printf("provider token usage: input=%d output=%d", result.Classification.InputTokens, result.Classification.OutputTokens)
		} else {
			log.Printf("provider token usage: not reported")
		}
		if result.Status == StatusUnroutable {
			log.Printf("ticket was not routed: %s", result.Reason)
		} else {
			log.Printf("selected child workflow: department=%s workflow-id=%s", result.Department, result.ChildWorkflowID)
			log.Printf("assignment: employee=%s assignment-id=%s acknowledgment-sla=%s", result.EmployeeID, result.AssignmentID, result.SLADuration)
		}
		log.Printf("completed result: ticket=%s status=%s", result.TicketID, result.Status)
		log.Print(ticketResultSummary(result))
	}

	return nil
}

func parentWorkflowID(ticketID string) string {
	return "ticket-routing-" + ticketID
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
	return fmt.Sprintf("Workflow status: Completed\nTicket ID: %s\nDepartment: %s\nAssigned employee: %s\nBusiness status: %s\nSLA met: %s\n",
		result.TicketID, department, employeeID, result.Status, slaMetLabel(result.Status, result.SLAMet))
}

func configuredClassifier(mode string) (string, interface{}, error) {
	provider := configuredProvider()

	if mode == "live-demo" && provider != "jev" {
		return "", nil, fmt.Errorf("live-demo requires explicit AI_PROVIDER=jev opt-in")
	}
	if mode == "live-batch" && provider != "jev" {
		return "", nil, fmt.Errorf("live-batch requires explicit AI_PROVIDER=jev opt-in")
	}
	if (mode == "demo" || mode == "start-ticket" || mode == "batch") && provider != "mock" {
		return "", nil, fmt.Errorf("%s is mock-only; use live-demo for one explicitly opted-in Jev ticket", mode)
	}

	switch provider {
	case "mock":
		return provider, ClassifyTicket, nil
	case "jev":
		if mode != "worker" {
			return provider, nil, nil
		}
		classifier, err := newJevClassifier(os.Getenv("TYPESAFE_API_KEY"), jevEndpoint, jevModel, &http.Client{Timeout: 20 * time.Second})
		if err != nil {
			return "", nil, err
		}
		return provider, classifier.ClassifyTicket, nil
	default:
		return "", nil, fmt.Errorf("unsupported AI_PROVIDER %q; use mock or jev", provider)
	}
}

func configuredProvider() string {
	provider := strings.ToLower(strings.TrimSpace(os.Getenv("AI_PROVIDER")))
	if provider == "" {
		return "mock"
	}
	return provider
}

func validateLiveTaskList(mode, provider, taskList string, taskListExplicit bool) error {
	if provider == "jev" && (mode == "worker" || mode == "live-demo") {
		if !taskListExplicit || strings.TrimSpace(taskList) == "" || taskList == TaskList {
			return fmt.Errorf("%s requires an explicit non-default -task-list dedicated to live classification", mode)
		}
	}
	return nil
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
