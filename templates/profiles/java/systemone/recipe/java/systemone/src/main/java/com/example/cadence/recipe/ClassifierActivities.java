package com.example.cadence.recipe;

import com.uber.cadence.activity.ActivityMethod;

public interface ClassifierActivities {
  @ActivityMethod(name = "__RECIPE_SLUG__.classify-text")
  Classification classifyText(RecipeInput input);
}
