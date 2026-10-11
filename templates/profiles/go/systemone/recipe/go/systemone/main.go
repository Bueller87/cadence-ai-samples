package main

import (
	"context"
	"errors"
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

	"go.uber.org/cadence"
	"go.uber.org/cadence/.gen/go/cadence/workflowserviceclient"
	"go.uber.org/cadence/activity"
	"go.uber.org/cadence/client"
	"go.uber.org/cadence/worker"
	"go.uber.org/yarpc"
	"go.uber.org/yarpc/transport/tchannel"
)

const (
	cadenceServiceName = "cadence-frontend"
	defaultAddress     = "127.0.0.1:7933"
	defaultDomain      = "cadence-ai-samples"
	defaultWorkflowID  = "__RECIPE_SLUG__-demo"
	defaultText        = "Investigate this urgent example request."
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
	flags.StringVar(&config.TaskList, "task-list", TaskList, "Cadence task list")
	switch config.Action {
	case "worker":
		flags.StringVar(&config.ClassifierID, "classifier-id", "laya-local", "classifiers.yaml ID for live mode")
		flags.StringVar(&config.CatalogDir, "catalog-dir", defaultCatalogDir, "directory containing classifiers.yaml")
		flags.BoolVar(&config.ConfirmLive, "confirm-live", false, "confirm live classifier API usage")
	case "start":
		flags.StringVar(&config.WorkflowID, "workflow-id", defaultWorkflowID, "stable Workflow ID")
		flags.StringVar(&config.Text, "text", defaultText, "text to classify")
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
		if err := start(context.Background(), client.NewClient(service, config.Domain, nil), config.TaskList, config.WorkflowID, config.Text); err != nil {
			log.Fatal(err)
		}
	}
}

func runWorker(service workflowserviceclient.Interface, domain, taskList, classifierID string, classifierActivity interface{}) error {
	w, err := worker.NewV2(service, domain, taskList, worker.Options{})
	if err != nil {
		return fmt.Errorf("create worker: %w", err)
	}
	w.RegisterWorkflow(__RECIPE_CLASS__Workflow)
	w.RegisterActivityWithOptions(classifierActivity, activity.RegisterOptions{Name: classificationActivityName()})
	log.Printf("__RECIPE_SLUG__ worker polling domain=%s task-list=%s classifier-id=%s", domain, taskList, classifierID)
	return w.Run()
}

func start(ctx context.Context, cadenceClient client.Client, taskList, workflowID, text string) error {
	run, err := cadenceClient.ExecuteWorkflow(ctx, startOptions(taskList, workflowID), __RECIPE_CLASS__Workflow, RecipeInput{Text: text})
	if err != nil {
		if cadence.IsWorkflowExecutionAlreadyStartedError(err) {
			return fmt.Errorf(
				"workflow-id=%s already has an open Run; wait for it to close or pass a different -workflow-id",
				workflowID,
			)
		}
		return fmt.Errorf("start workflow-id=%s: %w", workflowID, err)
	}
	fmt.Printf("started workflow-id=%s run-id=%s\n", run.GetID(), run.GetRunID())
	var result RecipeResult
	if err := run.Get(ctx, &result); err != nil {
		return fmt.Errorf("wait for workflow-id=%s: %w", workflowID, err)
	}
	fmt.Printf("classification label=%s confidence=%.3f model=%s\n", result.Classification.Label, result.Classification.Confidence, result.Classification.Model)
	return nil
}

func startOptions(taskList, workflowID string) client.StartWorkflowOptions {
	return client.StartWorkflowOptions{
		ID:                              workflowID,
		TaskList:                        taskList,
		ExecutionStartToCloseTimeout:    10 * time.Minute,
		DecisionTaskStartToCloseTimeout: 10 * time.Second,
		WorkflowIDReusePolicy:           client.WorkflowIDReusePolicyAllowDuplicate,
	}
}

// configuredClassifier returns the selected classifier and Worker Activity.
// Mock mode returns before reading catalogs or credentials.
func configuredClassifier(mode, classifierID, catalogDir string) (string, interface{}, error) {
	if mode == "mock" {
		return mockClassifierID, ClassifyText, nil
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
	classifier, err := newSystemOneClassifier(apiKey, config.Endpoint, config.Model, &http.Client{Timeout: 20 * time.Second})
	if err != nil {
		return "", nil, err
	}
	return classifierID, classifier.ClassifyText, nil
}

func classificationActivityName() string {
	return runtime.FuncForPC(reflect.ValueOf(ClassifyText).Pointer()).Name()
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
