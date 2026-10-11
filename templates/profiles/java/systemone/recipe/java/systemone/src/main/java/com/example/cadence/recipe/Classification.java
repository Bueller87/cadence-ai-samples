package com.example.cadence.recipe;

import java.util.Map;

public record Classification(
    String label, double confidence, Map<String, Double> probabilities, String model) {}
