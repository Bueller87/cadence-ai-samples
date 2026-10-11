package com.example.cadence.recipe;

import com.uber.cadence.activity.ActivityMethod;

public interface RecipeActivities {
  @ActivityMethod(name = "__RECIPE_SLUG__.assess")
  Assessment assess(String text);

  @ActivityMethod(name = "__RECIPE_SLUG__.follow-up")
  String followUp(String text);
}
