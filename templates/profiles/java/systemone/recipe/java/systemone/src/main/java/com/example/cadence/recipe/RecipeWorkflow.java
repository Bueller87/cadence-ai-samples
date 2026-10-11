package com.example.cadence.recipe;

import com.uber.cadence.workflow.WorkflowMethod;

public interface RecipeWorkflow {
  @WorkflowMethod(name = "__RECIPE_CLASS__Workflow")
  RecipeResult run(RecipeInput input);
}
