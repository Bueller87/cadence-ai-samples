package com.example.cadence.recipe;

import java.net.http.HttpClient;
import java.nio.file.Path;
import java.time.Duration;
import java.util.function.Supplier;

final class ClassifierFactory {
  private ClassifierFactory() {}

  static Selection configureWorker(
      String classifierId,
      Path catalogDirectory,
      Environment environment,
      CatalogSource catalogs,
      Supplier<HttpClient> httpClient) {
    String selected = classifierId == null || classifierId.isBlank() ? MockClassifier.ID : classifierId;
    if (MockClassifier.ID.equals(selected)) {
      return new Selection(selected, new MockClassifier());
    }

    ClassifierConfig config = catalogs.load(catalogDirectory, selected);
    String apiKey = "";
    if (ClassifierCatalog.TYPESAFE.equals(config.provider())) {
      apiKey = environment.read("CLASSIFIER_AI_KEY");
      if (apiKey == null || apiKey.isBlank()) {
        throw new ClassifierAuthenticationException(
            "classifier " + selected + " requires CLASSIFIER_AI_KEY");
      }
    }
    return new Selection(
        selected,
        new SystemOneClassifier(
            apiKey,
            config,
            httpClient == null
                ? HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(20)).build()
                : httpClient.get()));
  }

  record Selection(String classifierId, ClassifierActivities activity) {}

  @FunctionalInterface
  interface Environment {
    String read(String name);
  }

  @FunctionalInterface
  interface CatalogSource {
    ClassifierConfig load(Path directory, String classifierId);
  }
}
