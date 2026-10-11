package com.example.cadence.recipe;

import java.util.Locale;

final class SyntheticActivities implements RecipeActivities {
  @Override
  public Assessment assess(String text) {
    if (text == null || text.isBlank()) {
      throw new InvalidRequestException("text must not be empty");
    }
    boolean needed = text.toLowerCase(Locale.ROOT).contains("investigate");
    return new Assessment(needed, needed ? "keyword match" : "no follow-up keyword");
  }

  @Override
  public String followUp(String text) {
    return "Follow-up recorded for: " + text;
  }
}
