package main

import (
	"bytes"
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
	"go.uber.org/cadence"
	"go.uber.org/cadence/.gen/go/shared"
	"go.uber.org/cadence/activity"
	"go.uber.org/cadence/client"
	cadencemocks "go.uber.org/cadence/mocks"
	"go.uber.org/cadence/testsuite"
	"go.uber.org/cadence/workflow"
)

func TestWorkflowRunsFollowUpOnlyWhenNeeded(t *testing.T) {
	tests := []struct {
		name     string
		text     string
		followUp string
	}{
		{name: "skip", text: "Routine request."},
		{name: "run", text: "Investigate this request.", followUp: "Follow-up recorded for: Investigate this request."},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			env := newWorkflowEnvironment()
			env.ExecuteWorkflow(__RECIPE_CLASS__Workflow, RecipeInput{Text: test.text})

			require.True(t, env.IsWorkflowCompleted())
			require.NoError(t, env.GetWorkflowError())
			var result RecipeResult
			require.NoError(t, env.GetWorkflowResult(&result))
			require.Equal(t, test.followUp, result.FollowUp)
			require.Equal(t, test.followUp != "", result.Assessment.NeedsFollowUp)
		})
	}
}

func TestWorkflowPropagatesActivityFailureWithoutRetryingInvalidInput(t *testing.T) {
	env := newWorkflowEnvironment()
	env.OnActivity(assessActivityName, mock.Anything, "bad").Return(
		Assessment{}, cadence.NewCustomError(invalidRequestReason, "invalid"),
	).Once()

	env.ExecuteWorkflow(__RECIPE_CLASS__Workflow, RecipeInput{Text: "bad"})

	require.True(t, env.IsWorkflowCompleted())
	require.Error(t, env.GetWorkflowError())
	env.AssertExpectations(t)
}

func TestSyntheticActivitiesAreDeterministicAndOffline(t *testing.T) {
	assessment, err := Assess(context.Background(), "Please INVESTIGATE.")
	require.NoError(t, err)
	require.Equal(t, Assessment{NeedsFollowUp: true, Reason: "keyword match"}, assessment)

	assessment, err = Assess(context.Background(), "Routine.")
	require.NoError(t, err)
	require.Equal(t, Assessment{NeedsFollowUp: false, Reason: "no follow-up keyword"}, assessment)

	followUp, err := FollowUp(context.Background(), "x")
	require.NoError(t, err)
	require.Equal(t, "Follow-up recorded for: x", followUp)
}

func TestEmptyActivityInputIsNonRetriable(t *testing.T) {
	_, err := Assess(context.Background(), "  ")
	require.Error(t, err)
	var customError *cadence.CustomError
	require.ErrorAs(t, err, &customError)
	require.Equal(t, invalidRequestReason, customError.Reason())
}

func TestCommandDefaultsAndOverrides(t *testing.T) {
	worker, err := parseCommand([]string{"worker", "-mode", "mock"})
	require.NoError(t, err)
	require.Equal(t, "worker", worker.Action)
	require.Equal(t, "mock", worker.Mode)
	require.Equal(t, defaultDomain, worker.Domain)
	require.Equal(t, defaultTaskList, worker.TaskList)

	start, err := parseCommand([]string{
		"start", "-mode", "live", "-domain", "test-domain", "-task-list", "test-task-list",
		"-workflow-id", "test-workflow", "-text", "hello",
	})
	require.NoError(t, err)
	require.Equal(t, "start", start.Action)
	require.Equal(t, "live", start.Mode)
	require.Equal(t, "test-domain", start.Domain)
	require.Equal(t, "test-task-list", start.TaskList)
	require.Equal(t, "test-workflow", start.WorkflowID)
	require.Equal(t, "hello", start.Text)

}

func TestCommandRejectsInvalidConfiguration(t *testing.T) {
	for _, args := range [][]string{
		{},
		{"batch"},
		{"acknowledge"},
		{"worker"},
		{"start"},
		{"worker", "-mode", "stop"},
		{"start", "-workflow-id", ""},
		{"start", "-text", " "},
		{"worker", "extra"},
		{"worker", "-unknown"},
		{"-mode", "worker"},
	} {
		_, err := parseCommand(args)
		require.Errorf(t, err, "expected %v to fail", args)
	}
}

func TestStartUsesStableIDAndAllowsReuseAfterClosure(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	run := cadencemocks.NewWorkflowRun(t)
	run.On("GetID").Return(defaultWorkflowID).Once()
	run.On("GetRunID").Return("run-001").Once()
	cadenceClient.On(
		"ExecuteWorkflow",
		mock.Anything,
		mock.MatchedBy(func(options client.StartWorkflowOptions) bool {
			return options.ID == defaultWorkflowID &&
				options.TaskList == defaultTaskList &&
				options.WorkflowIDReusePolicy == client.WorkflowIDReusePolicyAllowDuplicate
		}),
		workflowName,
		RecipeInput{Text: defaultText},
	).Return(run, nil).Once()

	var output bytes.Buffer
	err := startWorkflow(context.Background(), cadenceClient, Config{
		Action: "start", Mode: "mock", Address: defaultAddress, Domain: defaultDomain,
		TaskList: defaultTaskList, WorkflowID: defaultWorkflowID, Text: defaultText,
	}, &output)

	require.NoError(t, err)
	require.Equal(t, "started workflow-id="+defaultWorkflowID+" run-id=run-001\n", output.String())
}

func TestStartReportsDuplicateOpenRun(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	cadenceClient.On(
		"ExecuteWorkflow", mock.Anything, mock.Anything, mock.Anything, mock.Anything,
	).Return(nil, &shared.WorkflowExecutionAlreadyStartedError{}).Once()

	err := startWorkflow(context.Background(), cadenceClient, Config{
		Action: "start", Mode: "mock", Address: defaultAddress, Domain: defaultDomain,
		TaskList: defaultTaskList, WorkflowID: defaultWorkflowID, Text: defaultText,
	}, &bytes.Buffer{})

	require.Error(t, err)
	require.Contains(t, err.Error(), "already has an open Run")
}

func TestStartWrapsOtherFailures(t *testing.T) {
	cadenceClient := cadencemocks.NewClient(t)
	cadenceClient.On(
		"ExecuteWorkflow", mock.Anything, mock.Anything, mock.Anything, mock.Anything,
	).Return(nil, errors.New("service unavailable")).Once()

	err := startWorkflow(context.Background(), cadenceClient, Config{
		Action: "start", Mode: "mock", Address: defaultAddress, Domain: defaultDomain,
		TaskList: defaultTaskList, WorkflowID: defaultWorkflowID, Text: defaultText,
	}, &bytes.Buffer{})

	require.EqualError(t, err, "start workflow: service unavailable")
}

func newWorkflowEnvironment() *testsuite.TestWorkflowEnvironment {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	env.RegisterWorkflowWithOptions(
		__RECIPE_CLASS__Workflow,
		workflow.RegisterOptions{Name: workflowName},
	)
	env.RegisterActivityWithOptions(Assess, activity.RegisterOptions{Name: assessActivityName})
	env.RegisterActivityWithOptions(FollowUp, activity.RegisterOptions{Name: followUpActivityName})
	return env
}
