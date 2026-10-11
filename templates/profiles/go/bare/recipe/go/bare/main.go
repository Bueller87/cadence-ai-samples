package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	"go.uber.org/cadence"
	"go.uber.org/cadence/.gen/go/cadence/workflowserviceclient"
	"go.uber.org/cadence/activity"
	"go.uber.org/cadence/client"
	"go.uber.org/cadence/worker"
	"go.uber.org/cadence/workflow"
	"go.uber.org/yarpc"
	"go.uber.org/yarpc/transport/tchannel"
)

const (
	cadenceServiceName = "cadence-frontend"
	defaultAddress     = "127.0.0.1:7933"
	defaultDomain      = "cadence-ai-samples"
	defaultTaskList    = "__RECIPE_SLUG__"
	defaultWorkflowID  = "__RECIPE_SLUG__-demo"
	defaultText        = "Investigate this example request."
)

type Config struct {
	Action     string
	Mode       string
	Address    string
	Domain     string
	TaskList   string
	WorkflowID string
	Text       string
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
	if config.Action == "start" {
		if strings.TrimSpace(config.WorkflowID) == "" {
			return errors.New("workflow ID is required")
		}
		if strings.TrimSpace(config.Text) == "" {
			return errors.New("text must not be empty")
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
	flags.StringVar(&config.TaskList, "task-list", defaultTaskList, "Cadence task list")
	if config.Action == "start" {
		flags.StringVar(&config.WorkflowID, "workflow-id", defaultWorkflowID, "Workflow ID")
		flags.StringVar(&config.Text, "text", defaultText, "synthetic request text")
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

	service, closeDispatcher, err := newServiceClient(config.Address)
	if err != nil {
		log.Fatal(err)
	}
	defer closeDispatcher()

	switch config.Action {
	case "worker":
		if err := runWorker(service, config.Domain, config.TaskList, config.Mode); err != nil {
			log.Fatal(err)
		}
	case "start":
		cadenceClient := client.NewClient(service, config.Domain, nil)
		if err := startWorkflow(context.Background(), cadenceClient, config, os.Stdout); err != nil {
			log.Fatal(err)
		}
	}
}

func runWorker(service workflowserviceclient.Interface, domain, taskList, mode string) error {
	cadenceWorker, err := worker.NewV2(service, domain, taskList, worker.Options{})
	if err != nil {
		return fmt.Errorf("create worker: %w", err)
	}
	cadenceWorker.RegisterWorkflowWithOptions(
		__RECIPE_CLASS__Workflow,
		workflow.RegisterOptions{Name: workflowName},
	)
	cadenceWorker.RegisterActivityWithOptions(Assess, activity.RegisterOptions{Name: assessActivityName})
	cadenceWorker.RegisterActivityWithOptions(FollowUp, activity.RegisterOptions{Name: followUpActivityName})
	log.Printf("__RECIPE_SLUG__ %s worker polling domain=%s task-list=%s with synthetic Activities", mode, domain, taskList)
	return cadenceWorker.Run()
}

type workflowStarter interface {
	ExecuteWorkflow(context.Context, client.StartWorkflowOptions, interface{}, ...interface{}) (client.WorkflowRun, error)
}

func startWorkflow(ctx context.Context, cadenceClient workflowStarter, config Config, output io.Writer) error {
	run, err := cadenceClient.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID:                              config.WorkflowID,
		TaskList:                        config.TaskList,
		ExecutionStartToCloseTimeout:    10 * time.Minute,
		DecisionTaskStartToCloseTimeout: 30 * time.Second,
		WorkflowIDReusePolicy:           client.WorkflowIDReusePolicyAllowDuplicate,
	}, workflowName, RecipeInput{Text: config.Text})
	if err != nil {
		if cadence.IsWorkflowExecutionAlreadyStartedError(err) {
			return fmt.Errorf(
				"workflow-id=%s already has an open Run; wait for it to close or pass a different -workflow-id",
				config.WorkflowID,
			)
		}
		return fmt.Errorf("start workflow: %w", err)
	}
	if _, err := fmt.Fprintf(
		output, "started workflow-id=%s run-id=%s\n", run.GetID(), run.GetRunID(),
	); err != nil {
		return fmt.Errorf("write start result: %w", err)
	}
	return nil
}

func newServiceClient(address string) (workflowserviceclient.Interface, func(), error) {
	transport, err := tchannel.NewChannelTransport(tchannel.ServiceName("__RECIPE_SLUG__-sample"))
	if err != nil {
		return nil, nil, fmt.Errorf("create tchannel transport: %w", err)
	}
	dispatcher := yarpc.NewDispatcher(yarpc.Config{
		Name: "__RECIPE_SLUG__-sample",
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
