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
import java.net.http.HttpClient;
import java.nio.file.Path;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.function.Supplier;

public final class RecipeApplication {
  static final String DEFAULT_ADDRESS = "127.0.0.1:7933";
  static final String DEFAULT_DOMAIN = "cadence-ai-samples";
  static final String DEFAULT_TASK_LIST = "__RECIPE_SLUG__";
  static final String DEFAULT_WORKFLOW_ID = "__RECIPE_SLUG__-demo";
  static final String DEFAULT_TEXT = "Investigate this urgent example request.";
  static final String DEFAULT_CATALOG_DIR = "../../../..";

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
      ClassifierFactory.Selection selection =
          prepareClassifier(
              command,
              System::getenv,
              (directory, classifierId) ->
                  new ClassifierCatalog().load(directory, classifierId),
              HttpClient::newHttpClient);
      runWorker(client, command.taskList(), selection);
      return;
    }
    start(client, command);
  }

  static ClassifierFactory.Selection prepareClassifier(
      Command command,
      ClassifierFactory.Environment environment,
      ClassifierFactory.CatalogSource catalogs,
      Supplier<HttpClient> httpClient) {
    if (!"worker".equals(command.action())) {
      return null;
    }
    if ("mock".equals(command.mode())) {
      return ClassifierFactory.configureWorker(
          MockClassifier.ID, null, environment, catalogs, httpClient);
    }
    if (!command.confirmLive()) {
      throw new IllegalArgumentException(
          "worker --mode live requires --confirm-live");
    }
    return ClassifierFactory.configureWorker(
            command.classifierId(),
            Path.of(command.catalogDir()),
            environment,
            catalogs,
            httpClient);
  }

  static void runWorker(
      WorkflowClient client, String taskList, ClassifierFactory.Selection selection) {
    WorkerFactory factory = WorkerFactory.newInstance(client);
    Worker worker = factory.newWorker(taskList);
    worker.registerWorkflowImplementationTypes(RecipeWorkflowImpl.class);
    worker.registerActivitiesImplementations(selection.activity());
    System.out.printf(
        "__RECIPE_SLUG__ worker polling domain=%s task-list=%s classifier-id=%s%n",
        client.getOptions().getDomain(), taskList, selection.classifierId());
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
      String text,
      String classifierId,
      String catalogDir,
      boolean confirmLive) {
    static Command parse(String[] args) {
      if (args.length == 0) {
        throw new IllegalArgumentException("missing action; use worker or start");
      }
      String action = args[0];
      if (!"worker".equals(action) && !"start".equals(action)) {
        throw new IllegalArgumentException("unsupported action; use worker or start");
      }
      Map<String, String> values = new HashMap<>();
      boolean confirmLive = false;
      for (int index = 1; index < args.length; ) {
        String flag = args[index++];
        if ("--confirm-live".equals(flag)) {
          if (confirmLive) {
            throw new IllegalArgumentException("repeated option --confirm-live");
          }
          confirmLive = true;
          continue;
        }
        if (!flag.equals("--mode")
            && !flag.equals("--address")
            && !flag.equals("--domain")
            && !flag.equals("--task-list")
            && !flag.equals("--workflow-id")
            && !flag.equals("--text")
            && !flag.equals("--classifier-id")
            && !flag.equals("--catalog-dir")) {
          throw new IllegalArgumentException("unknown option " + flag);
        }
        if (index >= args.length) {
          throw new IllegalArgumentException("missing value for " + flag);
        }
        if (values.put(flag, args[index++]) != null) {
          throw new IllegalArgumentException("repeated option " + flag);
        }
      }
      if ("start".equals(action)
          && (values.containsKey("--classifier-id") || values.containsKey("--catalog-dir"))) {
        throw new IllegalArgumentException(
            "start accepts business input only; classifier configuration belongs to worker");
      }
      Command command =
          new Command(
              action,
              values.get("--mode"),
              values.getOrDefault("--address", DEFAULT_ADDRESS),
              values.getOrDefault("--domain", DEFAULT_DOMAIN),
              values.getOrDefault("--task-list", DEFAULT_TASK_LIST),
              values.getOrDefault("--workflow-id", DEFAULT_WORKFLOW_ID),
              values.getOrDefault("--text", DEFAULT_TEXT),
              values.get("--classifier-id"),
              values.getOrDefault("--catalog-dir", DEFAULT_CATALOG_DIR),
              confirmLive);
      command.validate();
      return command;
    }

    private void validate() {
      if (!"mock".equals(mode) && !"live".equals(mode)) {
        throw new IllegalArgumentException("unsupported --mode; use mock or live");
      }
      if (address.isBlank()
          || domain.isBlank()
          || taskList.isBlank()) {
        throw new IllegalArgumentException("command values must not be blank");
      }
      if ("start".equals(action) && (workflowId.isBlank() || text.isBlank())) {
        throw new IllegalArgumentException("workflow ID and text are required for start");
      }
      if ("mock".equals(mode) && classifierId != null) {
        throw new IllegalArgumentException(
            "--classifier-id is only valid for worker --mode live");
      }
      if (confirmLive && !("worker".equals(action) && "live".equals(mode))) {
        throw new IllegalArgumentException(
            "--confirm-live is only valid for worker --mode live");
      }
      if ("worker".equals(action) && "live".equals(mode)) {
        if (!confirmLive) {
          throw new IllegalArgumentException(
              "worker --mode live requires --confirm-live");
        }
        if (classifierId == null || classifierId.isBlank()) {
          throw new IllegalArgumentException(
              "worker --mode live requires --classifier-id");
        }
        if (catalogDir.isBlank()) {
          throw new IllegalArgumentException(
              "worker --mode live requires --catalog-dir");
        }
      }
    }
  }
}
