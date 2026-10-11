package com.example.cadence.recipe;

import com.uber.cadence.WorkflowExecution;
import com.uber.cadence.WorkflowIdReusePolicy;
import com.uber.cadence.client.DuplicateWorkflowException;
import com.uber.cadence.client.WorkflowClient;
import com.uber.cadence.client.WorkflowClientOptions;
import com.uber.cadence.client.WorkflowOptions;
import com.uber.cadence.serviceclient.ClientOptions;
import com.uber.cadence.serviceclient.WorkflowServiceTChannel;
import com.uber.cadence.worker.Worker;
import com.uber.cadence.worker.WorkerFactory;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

public final class RecipeApplication {
  static final String DEFAULT_ADDRESS = "127.0.0.1:7933";
  static final String DEFAULT_DOMAIN = "cadence-ai-samples";
  static final String DEFAULT_TASK_LIST = "__RECIPE_SLUG__";
  static final String DEFAULT_WORKFLOW_ID = "__RECIPE_SLUG__-demo";
  static final String DEFAULT_TEXT = "Investigate this example request.";

  private RecipeApplication() {}

  public static void main(String[] args) {
    Command command = Command.parse(args);
    WorkflowServiceTChannel service =
        new WorkflowServiceTChannel(clientOptions(command.address()));
    WorkflowClient client =
        WorkflowClient.newInstance(
            service,
            WorkflowClientOptions.newBuilder().setDomain(command.domain()).build());
    if ("worker".equals(command.action())) {
      runWorker(client, command.taskList(), command.mode());
      return;
    }
    start(client, command);
  }

  static void runWorker(WorkflowClient client, String taskList, String mode) {
    WorkerFactory factory = WorkerFactory.newInstance(client);
    Worker worker = factory.newWorker(taskList);
    worker.registerWorkflowImplementationTypes(RecipeWorkflowImpl.class);
    worker.registerActivitiesImplementations(new SyntheticActivities());
    System.out.printf(
        "__RECIPE_SLUG__ worker polling domain=%s task-list=%s mode=%s%n",
        client.getOptions().getDomain(), taskList, mode);
    factory.start();
  }

  static WorkflowExecution start(WorkflowClient client, Command command) {
    RecipeWorkflow workflow =
        client.newWorkflowStub(
            RecipeWorkflow.class, startOptions(command.taskList(), command.workflowId()));
    try {
      WorkflowExecution execution =
          WorkflowClient.start(workflow::run, new RecipeInput(command.text()));
      System.out.printf(
          "started workflow-id=%s run-id=%s%n",
          execution.getWorkflowId(), execution.getRunId());
      return execution;
    } catch (DuplicateWorkflowException error) {
      throw new IllegalStateException(
          "workflow-id="
              + command.workflowId()
              + " already has an open Run; wait for it to close or pass a different --workflow-id",
          error);
    }
  }

  static WorkflowOptions startOptions(String taskList, String workflowId) {
    return new WorkflowOptions.Builder()
        .setWorkflowId(workflowId)
        .setTaskList(taskList)
        .setExecutionStartToCloseTimeout(Duration.ofMinutes(10))
        .setTaskStartToCloseTimeout(Duration.ofSeconds(30))
        .setWorkflowIdReusePolicy(WorkflowIdReusePolicy.AllowDuplicate)
        .build();
  }

  private static ClientOptions clientOptions(String address) {
    int separator = address.lastIndexOf(':');
    if (separator < 1 || separator == address.length() - 1) {
      throw new IllegalArgumentException("--address must use host:port");
    }
    return ClientOptions.newBuilder()
        .setHost(address.substring(0, separator))
        .setPort(Integer.parseInt(address.substring(separator + 1)))
        .build();
  }

  record Command(
      String action,
      String mode,
      String address,
      String domain,
      String taskList,
      String workflowId,
      String text) {
    static Command parse(String[] args) {
      if (args.length == 0) {
        throw new IllegalArgumentException("missing action; use worker or start");
      }
      String action = args[0];
      if (!"worker".equals(action) && !"start".equals(action)) {
        throw new IllegalArgumentException("unsupported action; use worker or start");
      }
      Map<String, String> values = new HashMap<>();
      for (int index = 1; index < args.length; index += 2) {
        if (index + 1 >= args.length) {
          throw new IllegalArgumentException("missing value for " + args[index]);
        }
        String flag = args[index];
        if (!flag.equals("--mode")
            && !flag.equals("--address")
            && !flag.equals("--domain")
            && !flag.equals("--task-list")
            && !flag.equals("--workflow-id")
            && !flag.equals("--text")) {
          throw new IllegalArgumentException("unknown option " + flag);
        }
        if (values.put(flag, args[index + 1]) != null) {
          throw new IllegalArgumentException("repeated option " + flag);
        }
      }
      Command command =
          new Command(
              action,
              values.get("--mode"),
              values.getOrDefault("--address", DEFAULT_ADDRESS),
              values.getOrDefault("--domain", DEFAULT_DOMAIN),
              values.getOrDefault("--task-list", DEFAULT_TASK_LIST),
              values.getOrDefault("--workflow-id", DEFAULT_WORKFLOW_ID),
              values.getOrDefault("--text", DEFAULT_TEXT));
      command.validate();
      return command;
    }

    private void validate() {
      if (!"mock".equals(mode) && !"live".equals(mode)) {
        throw new IllegalArgumentException("unsupported --mode; use mock or live");
      }
      if (address.isBlank() || domain.isBlank() || taskList.isBlank()) {
        throw new IllegalArgumentException("address, domain, and task list are required");
      }
      if ("start".equals(action) && (workflowId.isBlank() || text.isBlank())) {
        throw new IllegalArgumentException("workflow ID and text are required for start");
      }
    }
  }
}
