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
	defaultWorkflowID  = "__RECIPE_SLUG__-demo"
	defaultText        = "Investigate this urgent example request."
)

func main() {
	mode := flag.String("mode", "demo", "run mode: worker, demo, or live-demo")
	address := flag.String("address", "127.0.0.1:7933", "Cadence frontend address")
	domain := flag.String("domain", "cadence-ai-samples", "Cadence domain")
	taskList := flag.String("task-list", TaskList, "Cadence task list")
	workflowID := flag.String("workflow-id", defaultWorkflowID, "stable Workflow ID")
	text := flag.String("text", defaultText, "text to classify")
	classifierID := flag.String("classifier-id", mockClassifierID, "classifier: mock, or a classifiers.yaml ID such as jev-default or laya-local")
	catalogDir := flag.String("catalog-dir", defaultCatalogDir, "directory containing classifiers.yaml")
	confirmLive := flag.Bool("confirm-live", false, "confirm that this process may cause live classifier API usage")
	flag.Parse()

	selectedClassifier, classifierActivity, err := configuredClassifier(*mode, *classifierID, *catalogDir)
	if err != nil {
		log.Fatal(err)
	}
	if err := validateMode(*mode, selectedClassifier, *confirmLive); err != nil {
		log.Fatal(err)
	}

	service, closeDispatcher, err := newServiceClient(*address)
	if err != nil {
		log.Fatal(err)
	}
	defer closeDispatcher()

	switch *mode {
	case "worker":
		if err := runWorker(service, *domain, *taskList, selectedClassifier, classifierActivity); err != nil {
			log.Fatal(err)
		}
	case "demo":
		if err := start(context.Background(), client.NewClient(service, *domain, nil), *taskList, *workflowID, *text); err != nil {
			log.Fatal(err)
		}
	case "live-demo":
		if err := start(context.Background(), client.NewClient(service, *domain, nil), *taskList, *workflowID, *text); err != nil {
			log.Fatal(err)
		}
	default:
		fmt.Fprintf(os.Stderr, "unsupported mode %q; use worker, demo, or live-demo\n", *mode)
		os.Exit(2)
	}
}

func validateMode(mode, classifierID string, confirmLive bool) error {
	switch mode {
	case "worker", "demo", "live-demo":
	default:
		return fmt.Errorf("unsupported mode %q; use worker, demo, or live-demo", mode)
	}
	if classifierID != mockClassifierID && !confirmLive {
		return fmt.Errorf("%s with classifier %q requires -confirm-live", mode, classifierID)
	}
	return nil
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
// Starters validate catalog selections but never construct an HTTP client.
func configuredClassifier(mode, classifierID, catalogDir string) (string, interface{}, error) {
	classifierID = strings.TrimSpace(classifierID)
	if classifierID == "" {
		classifierID = mockClassifierID
	}
	live := classifierID != mockClassifierID

	if (mode == "live-demo") && !live {
		return "", nil, fmt.Errorf("%s requires an explicit live -classifier-id such as jev-default or laya-local", mode)
	}
	if (mode == "demo") && live {
		return "", nil, fmt.Errorf("%s is mock-only; use live-demo with -confirm-live for a live classifier", mode)
	}
	if !live {
		return classifierID, ClassifyText, nil
	}

	config, err := loadClassifier(catalogDir, classifierID)
	if err != nil {
		return "", nil, err
	}
	if mode != "worker" {
		return classifierID, nil, nil
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
