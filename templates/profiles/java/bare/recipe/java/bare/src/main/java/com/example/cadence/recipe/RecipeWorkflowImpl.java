package com.example.cadence.recipe;

import com.uber.cadence.common.RetryOptions;
import com.uber.cadence.activity.ActivityOptions;
import com.uber.cadence.workflow.Workflow;
import java.time.Duration;

public final class RecipeWorkflowImpl implements RecipeWorkflow {
  private final RecipeActivities activities =
      Workflow.newActivityStub(
          RecipeActivities.class,
          new ActivityOptions.Builder()
              .setScheduleToStartTimeout(Duration.ofMinutes(1))
              .setStartToCloseTimeout(Duration.ofSeconds(30))
              .setScheduleToCloseTimeout(Duration.ofMinutes(2))
              .setRetryOptions(
                  new RetryOptions.Builder()
                      .setInitialInterval(Duration.ofSeconds(1))
                      .setBackoffCoefficient(2)
                      .setMaximumInterval(Duration.ofSeconds(10))
                      .setMaximumAttempts(3)
                      .setDoNotRetry(InvalidRequestException.class)
                      .build())
              .build());

  @Override
  public RecipeResult run(RecipeInput input) {
    Assessment assessment = activities.assess(input.text());
    if (!assessment.needsFollowUp()) {
      return new RecipeResult(assessment, null);
    }
    return new RecipeResult(assessment, activities.followUp(input.text()));
  }
}

final class InvalidRequestException extends RuntimeException {
  InvalidRequestException(String message) {
    super(message);
  }
}
