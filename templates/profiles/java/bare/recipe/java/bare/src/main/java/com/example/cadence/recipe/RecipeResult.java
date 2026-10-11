package com.example.cadence.recipe;

import java.util.Objects;

public record RecipeResult(Assessment assessment, String followUp) {
  public RecipeResult {
    Objects.requireNonNull(assessment, "assessment");
  }
}
