package com.example.cadence.recipe;

import com.uber.cadence.activity.ActivityOptions;
import com.uber.cadence.common.RetryOptions;
import com.uber.cadence.workflow.Workflow;
import java.time.Duration;

public final class RecipeWorkflowImpl implements RecipeWorkflow {
  private final ClassifierActivities classifier =
      Workflow.newActivityStub(
          ClassifierActivities.class,
          new ActivityOptions.Builder()
              .setScheduleToStartTimeout(Duration.ofMinutes(1))
              .setStartToCloseTimeout(Duration.ofSeconds(30))
              .setScheduleToCloseTimeout(Duration.ofMinutes(2))
              .setRetryOptions(
                  new RetryOptions.Builder()
                      .setInitialInterval(Duration.ofSeconds(1))
                      .setBackoffCoefficient(2)
                      .setMaximumInterval(Duration.ofSeconds(10))
                      .setExpiration(Duration.ofMinutes(2))
                      .setDoNotRetry(
                          ClassifierConfigurationException.class,
                          ClassifierAuthenticationException.class,
                          ClassifierRequestException.class,
                          ClassifierResponseException.class)
                      .build())
              .build());

  @Override
  public RecipeResult run(RecipeInput input) {
    Classification result = classifier.classifyText(input);
    return new RecipeResult(input.text(), result);
  }
}

class ClassifierConfigurationException extends RuntimeException {
  ClassifierConfigurationException(String message) {
    super(message);
  }
}

class ClassifierAuthenticationException extends RuntimeException {
  ClassifierAuthenticationException(String message) {
    super(message);
  }
}

class ClassifierRequestException extends RuntimeException {
  ClassifierRequestException(String message) {
    super(message);
  }
}

class ClassifierResponseException extends RuntimeException {
  ClassifierResponseException(String message) {
    super(message);
  }
}
