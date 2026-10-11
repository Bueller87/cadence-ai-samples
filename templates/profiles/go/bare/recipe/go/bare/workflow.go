package main

import (
	"context"
	"strings"
	"time"

	"go.uber.org/cadence"
	"go.uber.org/cadence/workflow"
)

const (
	workflowName         = "__RECIPE_CLASS__Workflow"
	assessActivityName   = "__RECIPE_SLUG__.assess"
	followUpActivityName = "__RECIPE_SLUG__.follow-up"
	invalidRequestReason = "InvalidRequestError"
)

type RecipeInput struct {
	Text string `json:"text"`
}

type Assessment struct {
	NeedsFollowUp bool   `json:"needs_follow_up"`
	Reason        string `json:"reason"`
}

type RecipeResult struct {
	Assessment Assessment `json:"assessment"`
	FollowUp   string     `json:"follow_up,omitempty"`
}

func __RECIPE_CLASS__Workflow(ctx workflow.Context, input RecipeInput) (RecipeResult, error) {
	options := workflow.ActivityOptions{
		ScheduleToStartTimeout: time.Minute,
		StartToCloseTimeout:    30 * time.Second,
		ScheduleToCloseTimeout: 2 * time.Minute,
		RetryPolicy: &cadence.RetryPolicy{
			InitialInterval:          time.Second,
			BackoffCoefficient:       2,
			MaximumInterval:          10 * time.Second,
			MaximumAttempts:          3,
			NonRetriableErrorReasons: []string{invalidRequestReason},
		},
	}
	ctx = workflow.WithActivityOptions(ctx, options)

	var assessment Assessment
	if err := workflow.ExecuteActivity(ctx, assessActivityName, input.Text).Get(ctx, &assessment); err != nil {
		return RecipeResult{}, err
	}

	result := RecipeResult{Assessment: assessment}
	if !assessment.NeedsFollowUp {
		return result, nil
	}
	if err := workflow.ExecuteActivity(ctx, followUpActivityName, input.Text).Get(ctx, &result.FollowUp); err != nil {
		return RecipeResult{}, err
	}
	return result, nil
}

func Assess(_ context.Context, text string) (Assessment, error) {
	if strings.TrimSpace(text) == "" {
		return Assessment{}, cadence.NewCustomError(invalidRequestReason, "text must not be empty")
	}
	needed := strings.Contains(strings.ToLower(text), "investigate")
	reason := "no follow-up keyword"
	if needed {
		reason = "keyword match"
	}
	return Assessment{NeedsFollowUp: needed, Reason: reason}, nil
}

func FollowUp(_ context.Context, text string) (string, error) {
	return "Follow-up recorded for: " + text, nil
}
