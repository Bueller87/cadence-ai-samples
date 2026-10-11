package com.example.cadence.recipe;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.dataformat.yaml.YAMLMapper;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

final class ClassifierCatalog {
  static final String TYPESAFE = "typesafe";
  static final String LAYA = "laya";

  private final YAMLMapper yaml = new YAMLMapper();

  ClassifierConfig load(Path catalogDirectory, String classifierId) {
    Path path = catalogDirectory.resolve("classifiers.yaml");
    try {
      Catalog catalog = yaml.readValue(Files.readAllBytes(path), Catalog.class);
      if (catalog.classifiers() == null || catalog.classifiers().isEmpty()) {
        throw new ClassifierConfigurationException(
            "classifiers.yaml: expected a classifiers list");
      }
      Set<String> seen = new HashSet<>();
      ClassifierConfig selected = null;
      for (ClassifierConfig entry : catalog.classifiers()) {
        requireFields(entry);
        if (!seen.add(entry.id())) {
          throw new ClassifierConfigurationException(
              "classifiers.yaml: duplicate classifier ID " + entry.id());
        }
        if (entry.id().equals(classifierId)) {
          selected = entry;
        }
      }
      if (selected == null) {
        throw new ClassifierConfigurationException(
            "classifiers.yaml: unknown classifier ID " + classifierId);
      }
      validate(selected);
      return selected;
    } catch (IOException error) {
      throw new ClassifierConfigurationException(
          "read classifier catalog: " + error.getMessage());
    }
  }

  static void validate(ClassifierConfig config) {
    if (!TYPESAFE.equals(config.provider()) && !LAYA.equals(config.provider())) {
      throw new ClassifierConfigurationException(
          "classifier "
              + config.id()
              + " uses unsupported provider "
              + config.provider()
              + "; supported providers are typesafe and laya");
    }
    URI endpoint;
    try {
      endpoint = URI.create(config.endpoint());
    } catch (IllegalArgumentException error) {
      throw invalidEndpoint(config);
    }
    String scheme = endpoint.getScheme();
    if ((!"http".equals(scheme) && !"https".equals(scheme))
        || endpoint.getHost() == null
        || endpoint.getRawUserInfo() != null
        || endpoint.getRawQuery() != null
        || endpoint.getRawFragment() != null) {
      throw invalidEndpoint(config);
    }
    if (TYPESAFE.equals(config.provider()) && !"https".equals(scheme)) {
      throw new ClassifierConfigurationException(
          "classifier " + config.id() + " sends an API key and requires HTTPS");
    }
    if (LAYA.equals(config.provider()) && !isLoopback(endpoint.getHost())) {
      throw new ClassifierConfigurationException(
          "classifier " + config.id() + " requires a loopback endpoint");
    }
  }

  private static void requireFields(ClassifierConfig config) {
    if (config == null
        || blank(config.id())
        || blank(config.provider())
        || blank(config.model())
        || blank(config.endpoint())) {
      throw new ClassifierConfigurationException(
          "classifiers.yaml: every classifier needs id, provider, model, and endpoint");
    }
  }

  private static boolean blank(String value) {
    return value == null || value.isBlank();
  }

  private static boolean isLoopback(String host) {
    return "localhost".equals(host) || "127.0.0.1".equals(host) || "::1".equals(host);
  }

  private static ClassifierConfigurationException invalidEndpoint(ClassifierConfig config) {
    return new ClassifierConfigurationException(
        "classifier "
            + config.id()
            + " endpoint must be an HTTP URL without credentials, query, or fragment");
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  record Catalog(List<ClassifierConfig> classifiers) {}
}

@JsonIgnoreProperties(ignoreUnknown = true)
record ClassifierConfig(String id, String provider, String model, String endpoint) {}
