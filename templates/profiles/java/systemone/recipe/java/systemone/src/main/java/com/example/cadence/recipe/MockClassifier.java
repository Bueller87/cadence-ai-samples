package com.example.cadence.recipe;

import java.util.Locale;
import java.util.Map;

final class MockClassifier implements ClassifierActivities {
  static final String ID = "mock";

  @Override
  public Classification classifyText(RecipeInput input) {
    String text = input == null ? null : input.text();
    if (text == null || text.isBlank()) {
      throw new ClassifierRequestException("text is required");
    }
    String normalized = text.toLowerCase(Locale.ROOT);
    boolean priority =
        normalized.contains("urgent")
            || normalized.contains("blocked")
            || normalized.contains("outage")
            || normalized.contains("security");
    String label = priority ? "priority" : "routine";
    double confidence = priority ? 0.95 : 0.90;
    return new Classification(
        label, confidence, Map.of(label, confidence), "mock-systemone-v1");
  }
}
