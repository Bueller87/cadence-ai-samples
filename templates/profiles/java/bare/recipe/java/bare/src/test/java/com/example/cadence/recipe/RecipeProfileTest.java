package com.example.cadence.recipe;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.uber.cadence.WorkflowIdReusePolicy;
import com.uber.cadence.client.WorkflowOptions;
import com.uber.cadence.testing.TestWorkflowEnvironment;
import com.uber.cadence.worker.Worker;
import org.junit.jupiter.api.Test;

final class RecipeProfileTest {
  @Test
  void workflowRunsFollowUpOnlyWhenNeeded() {
    TestWorkflowEnvironment environment = TestWorkflowEnvironment.newInstance();
    try {
      Worker worker = environment.newWorker(RecipeApplication.DEFAULT_TASK_LIST);
      worker.registerWorkflowImplementationTypes(RecipeWorkflowImpl.class);
      worker.registerActivitiesImplementations(new SyntheticActivities());
      environment.start();
      RecipeWorkflow workflow =
          environment
              .newWorkflowClient()
              .newWorkflowStub(
                  RecipeWorkflow.class,
                  RecipeApplication.startOptions(
                      RecipeApplication.DEFAULT_TASK_LIST, "test-follow-up"));

      RecipeResult result = workflow.run(new RecipeInput("Investigate this request."));

      assertThat(result.assessment().needsFollowUp()).isTrue();
      assertThat(result.followUp()).isEqualTo("Follow-up recorded for: Investigate this request.");
    } finally {
      environment.close();
    }
  }

  @Test
  void syntheticActivitiesAreOfflineAndValidateInput() {
    SyntheticActivities activities = new SyntheticActivities();
    assertThat(activities.assess("Routine request."))
        .isEqualTo(new Assessment(false, "no follow-up keyword"));
    assertThatThrownBy(() -> activities.assess(" "))
        .isInstanceOf(InvalidRequestException.class);
  }

  @Test
  void commandDefaultsOverridesAndValidationAreLiteral() {
    RecipeApplication.Command worker =
        RecipeApplication.Command.parse(new String[] {"worker", "--mode", "mock"});
    assertThat(worker.action()).isEqualTo("worker");
    assertThat(worker.mode()).isEqualTo("mock");
    assertThat(worker.domain()).isEqualTo("cadence-ai-samples");
    assertThat(worker.taskList()).isEqualTo("__RECIPE_SLUG__");

    RecipeApplication.Command start =
        RecipeApplication.Command.parse(
            new String[] {
              "start",
              "--mode", "live",
              "--domain", "test-domain",
              "--task-list", "test-task-list",
              "--workflow-id", "test-workflow",
              "--text", "hello"
            });
    assertThat(start.action()).isEqualTo("start");
    assertThat(start.mode()).isEqualTo("live");
    assertThat(start.workflowId()).isEqualTo("test-workflow");
    assertThatThrownBy(
            () -> RecipeApplication.Command.parse(new String[] {"stop", "--mode", "mock"}))
        .isInstanceOf(IllegalArgumentException.class);
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {"start", "--mode", "mock", "--text", " "}))
        .isInstanceOf(IllegalArgumentException.class);
    assertThatThrownBy(
            () -> RecipeApplication.Command.parse(new String[] {"worker", "-mode", "mock"}))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void stableRunIdExplicitlyAllowsReuseOnlyAfterClosure() {
    WorkflowOptions options =
        RecipeApplication.startOptions(
            RecipeApplication.DEFAULT_TASK_LIST, RecipeApplication.DEFAULT_WORKFLOW_ID);
    assertThat(options.getWorkflowId()).isEqualTo("__RECIPE_SLUG__-demo");
    assertThat(options.getWorkflowIdReusePolicy())
        .isEqualTo(WorkflowIdReusePolicy.AllowDuplicate);
    assertThat(options.getWorkflowIdReusePolicy())
        .isNotEqualTo(WorkflowIdReusePolicy.TerminateIfRunning);
  }
}
