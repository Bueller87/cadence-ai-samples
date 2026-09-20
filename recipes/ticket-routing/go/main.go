package main

import (
	"context"
	"flag"
	"fmt"
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

const cadenceServiceName = "cadence-frontend"

func main() {
	mode := flag.String("mode", "demo", "run mode: worker, demo, or live-demo")
	address := flag.String("address", "127.0.0.1:7933", "Cadence frontend address")
	domain := flag.String("domain", "cadence-ai-samples", "Cadence domain")
	taskList := flag.String("task-list", TaskList, "Cadence task list")
	flag.Parse()

	provider, classifierActivity, err := configuredClassifier(*mode)
	if err != nil {
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
		if err := runDemo(context.Background(), cadenceClient, *taskList); err != nil {
			log.Fatal(err)
		}
	case "live-demo":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := runLiveDemo(context.Background(), cadenceClient, *taskList); err != nil {
			log.Fatal(err)
		}
	default:
		fmt.Fprintf(os.Stderr, "unsupported mode %q; use worker, demo, or live-demo\n", *mode)
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

func runDemo(ctx context.Context, cadenceClient client.Client, taskList string) error {
	tickets := []Ticket{
		{TicketID: "demo-billing-001", Message: "I was charged twice for my monthly subscription."},
		{TicketID: "demo-technical-002", Message: "The app keeps crashing on multiple devices."},
		{TicketID: "demo-account-003", Message: "I am locked out and cannot reset my password."},
		{TicketID: "demo-content-004", Message: "The subtitles are missing from the newest episode."},
	}

	return runTickets(ctx, cadenceClient, taskList, "mock", tickets)
}

func runLiveDemo(ctx context.Context, cadenceClient client.Client, taskList string) error {
	ticketID := "live-jev-" + time.Now().UTC().Format("20060102T150405.000000000")
	tickets := []Ticket{{
		TicketID: ticketID,
		Message:  "I was charged twice for my StreamWave subscription and need help before my next billing date.",
	}}
	return runTickets(ctx, cadenceClient, taskList, "real Jev", tickets)
}

func runTickets(ctx context.Context, cadenceClient client.Client, taskList, classificationSource string, tickets []Ticket) error {
	for _, ticket := range tickets {
		log.Printf("incoming request: ticket=%s message=%q", ticket.TicketID, ticket.Message)

		run, err := cadenceClient.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
			ID:                              "ticket-routing-" + ticket.TicketID,
			TaskList:                        taskList,
			ExecutionStartToCloseTimeout:    2 * time.Minute,
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
			log.Printf("assigned employee: %s", result.EmployeeID)
		}
		log.Printf("completed result: ticket=%s status=%s", result.TicketID, result.Status)
	}

	return nil
}

func configuredClassifier(mode string) (string, interface{}, error) {
	provider := strings.ToLower(strings.TrimSpace(os.Getenv("AI_PROVIDER")))
	if provider == "" {
		provider = "mock"
	}

	if mode == "live-demo" && provider != "jev" {
		return "", nil, fmt.Errorf("live-demo requires explicit AI_PROVIDER=jev opt-in")
	}
	if mode == "demo" && provider != "mock" {
		return "", nil, fmt.Errorf("demo is mock-only; use live-demo for one explicitly opted-in Jev ticket")
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
