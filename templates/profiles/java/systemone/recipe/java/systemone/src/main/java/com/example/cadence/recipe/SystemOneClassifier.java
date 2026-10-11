package com.example.cadence.recipe;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

final class SystemOneClassifier implements ClassifierActivities {
  private final String apiKey;
  private final URI endpoint;
  private final String model;
  private final HttpClient httpClient;
  private final ObjectMapper json =
      new ObjectMapper().enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

  SystemOneClassifier(
      String apiKey, ClassifierConfig config, HttpClient httpClient) {
    this.apiKey = apiKey == null ? "" : apiKey.trim();
    this.endpoint = URI.create(config.endpoint());
    this.model = config.model();
    this.httpClient = httpClient;
  }

  @Override
  public Classification classifyText(RecipeInput input) {
    if (input == null || input.text() == null || input.text().isBlank()) {
      throw new ClassifierRequestException("text is required");
    }
    SystemOneRequest payload =
        new SystemOneRequest(
            input.text(),
            model,
            Map.of(
                "route",
                new ChoiceQuestion(
                    "choice",
                    "Should this request receive routine handling or priority review?",
                    Map.of(
                        "routine",
                        "Normal request without urgent operational or security impact.",
                        "priority",
                        "Urgent, blocked, outage-related, or security-sensitive request."))));
    HttpRequest.Builder request =
        HttpRequest.newBuilder(endpoint)
            .timeout(Duration.ofSeconds(20))
            .header("Content-Type", "application/json");
    if (!apiKey.isEmpty()) {
      request.header("Authorization", "Bearer " + apiKey);
    }
    try {
      HttpResponse<String> response =
          httpClient.send(
              request.POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(payload)))
                  .build(),
              HttpResponse.BodyHandlers.ofString());
      return decodeResponse(response);
    } catch (JsonProcessingException error) {
      throw new ClassifierRequestException("encode classifier request");
    } catch (IOException error) {
      throw new RuntimeException("classifier request failed", error);
    } catch (InterruptedException error) {
      Thread.currentThread().interrupt();
      throw new RuntimeException("classifier request interrupted", error);
    }
  }

  private Classification decodeResponse(HttpResponse<String> response) {
    int status = response.statusCode();
    if (status == 401) {
      throw new ClassifierAuthenticationException(
          "classifier rejected the configured API key");
    }
    if (status == 429 || status == 529 || status >= 500) {
      throw new RuntimeException("classifier temporary HTTP failure: " + status);
    }
    if (status < 200 || status >= 300) {
      throw new ClassifierRequestException("classifier returned HTTP " + status);
    }
    try {
      SystemOneResponse decoded = json.readValue(response.body(), SystemOneResponse.class);
      ChoiceAnswer answer = decoded.answers() == null ? null : decoded.answers().get("route");
      if (blank(decoded.model()) || answer == null || !"choice".equals(answer.type())) {
        throw new ClassifierResponseException("missing model or route choice");
      }
      if (!"routine".equals(answer.choice()) && !"priority".equals(answer.choice())) {
        throw new ClassifierResponseException("unsupported route choice");
      }
      validateProbability(answer.confidence(), "invalid route confidence");
      if (answer.probabilities() == null
          || answer.probabilities().size() != 2
          || !answer.probabilities().containsKey("routine")
          || !answer.probabilities().containsKey("priority")) {
        throw new ClassifierResponseException(
            "route probabilities must contain routine and priority");
      }
      double sum = 0;
      for (double probability : answer.probabilities().values()) {
        validateProbability(probability, "invalid route probabilities");
        sum += probability;
      }
      if (Math.abs(sum - 1) > 0.02) {
        throw new ClassifierResponseException("route probabilities do not sum to one");
      }
      return new Classification(
          answer.choice(),
          answer.confidence(),
          Map.copyOf(answer.probabilities()),
          decoded.model());
    } catch (JsonProcessingException | NullPointerException error) {
      throw new ClassifierResponseException("decode response JSON");
    }
  }

  private static void validateProbability(Double value, String message) {
    if (value == null || !Double.isFinite(value) || value < 0 || value > 1) {
      throw new ClassifierResponseException(message);
    }
  }

  private static boolean blank(String value) {
    return value == null || value.isBlank();
  }

  record SystemOneRequest(
      String state, String model, Map<String, ChoiceQuestion> questions) {}

  record ChoiceQuestion(
      String type, String instructions, Map<String, String> criteria) {}

  record SystemOneResponse(String model, Map<String, ChoiceAnswer> answers) {}

  record ChoiceAnswer(
      String type, String choice, Map<String, Double> probabilities, Double confidence) {}
}
