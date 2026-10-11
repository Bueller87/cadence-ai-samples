package com.example.cadence.recipe;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.sun.net.httpserver.HttpServer;
import com.uber.cadence.WorkflowIdReusePolicy;
import com.uber.cadence.client.WorkflowOptions;
import com.uber.cadence.testing.TestWorkflowEnvironment;
import com.uber.cadence.worker.Worker;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

final class SystemOneProfileTest {
  @Test
  void workflowRecordsMockClassification() {
    TestWorkflowEnvironment environment = TestWorkflowEnvironment.newInstance();
    try {
      Worker worker = environment.newWorker(RecipeApplication.DEFAULT_TASK_LIST);
      worker.registerWorkflowImplementationTypes(RecipeWorkflowImpl.class);
      worker.registerActivitiesImplementations(new MockClassifier());
      environment.start();
      RecipeWorkflow workflow =
          environment
              .newWorkflowClient()
              .newWorkflowStub(
                  RecipeWorkflow.class,
                  RecipeApplication.startOptions(
                      RecipeApplication.DEFAULT_TASK_LIST, "test-systemone"));

      RecipeResult result = workflow.run(new RecipeInput("Urgent security outage."));

      assertThat(result.classification().label()).isEqualTo("priority");
      assertThat(result.classification().model()).isEqualTo("mock-systemone-v1");
    } finally {
      environment.close();
    }
  }

  @Test
  void mockPathReadsNoCatalogOrCredential() {
    ClassifierFactory.Selection selection =
        RecipeApplication.prepareClassifier(
            RecipeApplication.Command.parse(
                new String[] {
                  "worker", "--mode", "mock", "--catalog-dir", "does-not-exist"
                }),
            name -> {
              throw new AssertionError("mock path read credential " + name);
            },
            (directory, id) -> {
              throw new AssertionError("mock path read catalog");
            },
            () -> {
              throw new AssertionError("mock path constructed HTTP client");
            });

    assertThat(selection.activity()).isInstanceOf(MockClassifier.class);
    assertThat(selection.activity().classifyText(new RecipeInput("Routine request.")).label())
        .isEqualTo("routine");
  }

  @Test
  void liveGuardRunsBeforeWorkerSetup() {
    RecipeApplication.Command unconfirmed =
        new RecipeApplication.Command(
            "worker",
            "live",
            RecipeApplication.DEFAULT_ADDRESS,
            RecipeApplication.DEFAULT_DOMAIN,
            RecipeApplication.DEFAULT_TASK_LIST,
            RecipeApplication.DEFAULT_WORKFLOW_ID,
            RecipeApplication.DEFAULT_TEXT,
            "jev-default",
            "does-not-exist",
            false);

    assertThatThrownBy(
            () ->
                RecipeApplication.prepareClassifier(
                    unconfirmed,
                    name -> {
                      throw new AssertionError("unconfirmed worker read credential " + name);
                    },
                    (directory, id) -> {
                      throw new AssertionError("unconfirmed worker read catalog");
                    },
                    () -> {
                      throw new AssertionError("unconfirmed worker constructed HTTP client");
                    }))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("requires --confirm-live");
  }

  @Test
  void catalogSelectionAndEndpointSafety(@TempDir Path directory) throws IOException {
    writeCatalog(
        directory,
        """
        classifiers:
          - id: jev-default
            provider: typesafe
            model: jev-latest
            endpoint: https://classifier.example/v1/systemone
          - id: laya-local
            provider: laya
            model: local-model
            endpoint: http://localhost:8008/v1/systemone
        """);
    ClassifierConfig selected = new ClassifierCatalog().load(directory, "jev-default");
    assertThat(selected.model()).isEqualTo("jev-latest");

    assertThatThrownBy(
            () ->
                ClassifierCatalog.validate(
                    new ClassifierConfig(
                        "insecure", "typesafe", "m", "http://classifier.example/path")))
        .isInstanceOf(ClassifierConfigurationException.class);
    assertThatThrownBy(
            () ->
                ClassifierCatalog.validate(
                    new ClassifierConfig(
                        "remote", "laya", "m", "https://classifier.example/path")))
        .isInstanceOf(ClassifierConfigurationException.class);
    for (String endpoint :
        new String[] {
          "http://user:pass@localhost:8008/path",
          "http://localhost:8008/path?query=x",
          "http://localhost:8008/path#fragment",
          "file:///tmp/classifier"
        }) {
      assertThatThrownBy(
              () ->
                  ClassifierCatalog.validate(
                      new ClassifierConfig("unsafe", "laya", "m", endpoint)))
          .isInstanceOf(ClassifierConfigurationException.class);
    }
  }

  @Test
  void changedCatalogEndpointReachesLocalTransport(@TempDir Path directory)
      throws Exception {
    AtomicReference<URI> requested = new AtomicReference<>();
    HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/changed-systemone",
        exchange -> {
          requested.set(exchange.getRequestURI());
          byte[] body =
              """
              {"model":"changed-model","answers":{"route":{"type":"choice",
              "choice":"routine","probabilities":{"routine":0.8,"priority":0.2},
              "confidence":0.8}}}
              """
                  .getBytes(StandardCharsets.UTF_8);
          exchange.getResponseHeaders().set("Content-Type", "application/json");
          exchange.sendResponseHeaders(200, body.length);
          exchange.getResponseBody().write(body);
          exchange.close();
        });
    server.start();
    try {
      writeCatalog(
          directory,
          "classifiers:\n"
              + "  - id: laya-local\n"
              + "    provider: laya\n"
              + "    model: changed-model\n"
              + "    endpoint: http://127.0.0.1:"
              + server.getAddress().getPort()
              + "/changed-systemone\n");
      ClassifierCatalog catalog = new ClassifierCatalog();
      ClassifierFactory.Selection selection =
          RecipeApplication.prepareClassifier(
              RecipeApplication.Command.parse(
                  new String[] {
                    "worker",
                    "--mode", "live",
                    "--classifier-id", "laya-local",
                    "--catalog-dir", directory.toString(),
                    "--confirm-live"
                  }),
              name -> {
                throw new AssertionError("local provider read credential " + name);
              },
              catalog::load,
              HttpClient::newHttpClient);

      Classification result =
          selection.activity().classifyText(new RecipeInput("Synthetic request."));

      assertThat(requested.get().getPath()).isEqualTo("/changed-systemone");
      assertThat(result.model()).isEqualTo("changed-model");
    } finally {
      server.stop(0);
    }
  }

  @Test
  void startsReadOnlyBusinessInputAndSkipWorkerConfiguration() {
    for (RecipeApplication.Command command :
        new RecipeApplication.Command[] {
          RecipeApplication.Command.parse(
              new String[] {"start", "--mode", "mock"}),
          RecipeApplication.Command.parse(
              new String[] {"start", "--mode", "live"})
        }) {
      ClassifierFactory.Selection selection =
          RecipeApplication.prepareClassifier(
              command,
              name -> {
                throw new AssertionError("start read credential " + name);
              },
              (directory, id) -> {
                throw new AssertionError("start read catalog");
              },
              () -> {
                throw new AssertionError("start constructed classifier HTTP client");
              });
      assertThat(selection).isNull();
    }
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {
                      "start", "--mode", "live", "--classifier-id", "jev-default"
                    }))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("classifier configuration belongs to worker");
  }

  @Test
  void duplicateStartIsRejectedWhileFirstRunIsOpen() throws Exception {
    TestWorkflowEnvironment environment = TestWorkflowEnvironment.newInstance();
    CountDownLatch activityStarted = new CountDownLatch(1);
    CountDownLatch releaseActivity = new CountDownLatch(1);
    try {
      Worker worker = environment.newWorker(RecipeApplication.DEFAULT_TASK_LIST);
      worker.registerWorkflowImplementationTypes(RecipeWorkflowImpl.class);
      worker.registerActivitiesImplementations(
          new ClassifierActivities() {
            @Override
            public Classification classifyText(RecipeInput input) {
              activityStarted.countDown();
              try {
                if (!releaseActivity.await(5, TimeUnit.SECONDS)) {
                  throw new IllegalStateException("test activity was not released");
                }
              } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("test activity interrupted", error);
              }
              return new MockClassifier().classifyText(input);
            }
          });
      environment.start();
      RecipeApplication.Command command =
          RecipeApplication.Command.parse(
              new String[] {
                "start", "--mode", "mock", "--workflow-id", "duplicate-open"
              });

      RecipeApplication.start(environment.newWorkflowClient(), command);
      assertThat(activityStarted.await(5, TimeUnit.SECONDS)).isTrue();
      assertThatThrownBy(
              () -> RecipeApplication.start(environment.newWorkflowClient(), command))
          .isInstanceOf(IllegalStateException.class)
          .hasMessageContaining("already has an open Run");
    } finally {
      releaseActivity.countDown();
      environment.close();
    }
  }

  @Test
  void liveWorkerConfigurationAndCredentialsAreWorkerLocal(@TempDir Path directory)
      throws IOException {
    writeCatalog(
        directory,
        """
        classifiers:
          - id: jev-default
            provider: typesafe
            model: jev-latest
            endpoint: https://classifier.example/v1/systemone
        """);
    ClassifierCatalog catalog = new ClassifierCatalog();
    assertThatThrownBy(
            () ->
                ClassifierFactory.configureWorker(
                    "jev-default",
                    directory,
                    name -> "",
                    catalog::load,
                    null))
        .isInstanceOf(ClassifierAuthenticationException.class);
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {"worker", "--mode", "live"}))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("requires --confirm-live");
  }

  @Test
  void workflowInputCannotCarryWorkerConfiguration() {
    Set<String> fields =
        Arrays.stream(RecipeInput.class.getDeclaredFields())
            .map(field -> field.getName())
            .collect(Collectors.toSet());
    assertThat(fields).containsExactly("text");
    assertThat(fields)
        .doesNotContain(
            "endpoint", "apiKey", "credential", "classifierId", "catalogDir", "provider", "model");
  }

  @Test
  void commandAndReusePolicyMatchDocumentedContract() {
    RecipeApplication.Command mockWorker =
        RecipeApplication.Command.parse(new String[] {"worker", "--mode", "mock"});
    assertThat(mockWorker.action()).isEqualTo("worker");
    assertThat(mockWorker.mode()).isEqualTo("mock");
    assertThat(mockWorker.classifierId()).isNull();
    RecipeApplication.Command liveWorker =
        RecipeApplication.Command.parse(
            new String[] {
              "worker", "--mode", "live",
              "--classifier-id", "laya-local",
              "--confirm-live"
            });
    assertThat(liveWorker.action()).isEqualTo("worker");
    assertThat(liveWorker.mode()).isEqualTo("live");
    assertThat(liveWorker.classifierId()).isEqualTo("laya-local");
    assertThat(liveWorker.confirmLive()).isTrue();
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {"worker", "--mode", "mock", "--classifier-id", "laya-local"}))
        .isInstanceOf(IllegalArgumentException.class);
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {"worker", "--mode", "mock", "--confirm-live"}))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("only valid for worker --mode live");
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {"start", "--mode", "live", "--confirm-live"}))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("only valid for worker --mode live");
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {
                      "worker",
                      "--mode", "live",
                      "--classifier-id", "laya-local",
                      "--confirm-live",
                      "--confirm-live"
                    }))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("repeated option --confirm-live");
    assertThatThrownBy(
            () ->
                RecipeApplication.Command.parse(
                    new String[] {"worker", "-mode", "mock"}))
        .isInstanceOf(IllegalArgumentException.class);

    WorkflowOptions options =
        RecipeApplication.startOptions(
            RecipeApplication.DEFAULT_TASK_LIST, RecipeApplication.DEFAULT_WORKFLOW_ID);
    assertThat(options.getWorkflowId()).isEqualTo("__RECIPE_SLUG__-demo");
    assertThat(options.getWorkflowIdReusePolicy())
        .isEqualTo(WorkflowIdReusePolicy.AllowDuplicate);
    assertThat(options.getWorkflowIdReusePolicy())
        .isNotEqualTo(WorkflowIdReusePolicy.TerminateIfRunning);
  }

  private static void writeCatalog(Path directory, String contents) throws IOException {
    Files.writeString(directory.resolve("classifiers.yaml"), contents);
  }
}
