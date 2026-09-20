package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	"go.uber.org/cadence/.gen/go/cadence/workflowserviceclient"
	"go.uber.org/cadence/client"
	"go.uber.org/cadence/worker"
	"go.uber.org/yarpc"
	"go.uber.org/yarpc/transport/tchannel"
)

const cadenceServiceName = "cadence-frontend"

func main() {
	mode := flag.String("mode", "demo", "run mode: worker or demo")
	address := flag.String("address", "127.0.0.1:7933", "Cadence frontend address")
	domain := flag.String("domain", "cadence-ai-samples", "Cadence domain")
	taskList := flag.String("task-list", TaskList, "Cadence task list")
	flag.Parse()

	service, closeDispatcher, err := newServiceClient(*address)
	if err != nil {
		log.Fatal(err)
	}
	defer closeDispatcher()

	switch *mode {
	case "worker":
		if err := runWorker(service, *domain, *taskList); err != nil {
			log.Fatal(err)
		}
	case "demo":
		cadenceClient := client.NewClient(service, *domain, nil)
		if err := runDemo(context.Background(), cadenceClient, *taskList); err != nil {
			log.Fatal(err)
		}
	default:
		fmt.Fprintf(os.Stderr, "unsupported mode %q; use worker or demo\n", *mode)
		os.Exit(2)
	}
}

func runWorker(service workflowserviceclient.Interface, domain, taskList string) error {
	w, err := worker.NewV2(service, domain, taskList, worker.Options{})
	if err != nil {
		return fmt.Errorf("create worker: %w", err)
	}

	w.RegisterWorkflow(TicketIntakeWorkflow)
	w.RegisterWorkflow(BillingWorkflow)
	w.RegisterWorkflow(TechnicalWorkflow)
	w.RegisterWorkflow(AccountWorkflow)
	w.RegisterWorkflow(ContentWorkflow)
	w.RegisterActivity(ClassifyTicket)

	log.Printf("ticket-routing worker polling domain=%s task-list=%s", domain, taskList)
	return w.Run()
}

func runDemo(ctx context.Context, cadenceClient client.Client, taskList string) error {
	tickets := []Ticket{
		{TicketID: "demo-billing-001", Message: "I was charged twice for my monthly subscription."},
		{TicketID: "demo-technical-002", Message: "The app keeps crashing on multiple devices."},
		{TicketID: "demo-account-003", Message: "I am locked out and cannot reset my password."},
		{TicketID: "demo-content-004", Message: "The subtitles are missing from the newest episode."},
	}

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

		log.Printf("mock classification: department=%s priority=%s complexity=%s model=%s", result.Classification.Department, result.Classification.Priority, result.Classification.Complexity, result.Classification.Model)
		log.Printf("selected child workflow: department=%s workflow-id=%s", result.Department, result.ChildWorkflowID)
		log.Printf("assigned employee: %s", result.EmployeeID)
		log.Printf("completed result: ticket=%s status=%s", result.TicketID, result.Status)
	}

	return nil
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
