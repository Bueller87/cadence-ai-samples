package main

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

const (
	mockClassifierID           = "mock"
	defaultCatalogDir          = "../../.."
	classifierProviderTypeSafe = "typesafe"
	classifierProviderLaya     = "laya"
)

// ClassifierConfig is one entry from the repository-root classifiers.yaml.
type ClassifierConfig struct {
	ID       string `yaml:"id"`
	Provider string `yaml:"provider"`
	Model    string `yaml:"model"`
	Endpoint string `yaml:"endpoint"`
}

type classifierCatalog struct {
	Classifiers []ClassifierConfig `yaml:"classifiers"`
}

// loadClassifier selects one implemented classifier from catalogDir/classifiers.yaml.
func loadClassifier(catalogDir, classifierID string) (ClassifierConfig, error) {
	data, err := os.ReadFile(filepath.Join(catalogDir, "classifiers.yaml"))
	if err != nil {
		return ClassifierConfig{}, fmt.Errorf("read classifier catalog: %w", err)
	}
	var catalog classifierCatalog
	if err := yaml.Unmarshal(data, &catalog); err != nil {
		return ClassifierConfig{}, fmt.Errorf("parse classifiers.yaml: %w", err)
	}
	if len(catalog.Classifiers) == 0 {
		return ClassifierConfig{}, fmt.Errorf("classifiers.yaml: expected a classifiers list")
	}

	seen := make(map[string]bool, len(catalog.Classifiers))
	var selected *ClassifierConfig
	for index := range catalog.Classifiers {
		entry := catalog.Classifiers[index]
		if field := missingClassifierField(entry); field != "" {
			return ClassifierConfig{}, fmt.Errorf("classifiers.yaml: classifiers[%d] is missing %q", index, field)
		}
		if seen[entry.ID] {
			return ClassifierConfig{}, fmt.Errorf("classifiers.yaml: duplicate classifier ID %q", entry.ID)
		}
		seen[entry.ID] = true
		if entry.ID == classifierID {
			selected = &catalog.Classifiers[index]
		}
	}
	if selected == nil {
		return ClassifierConfig{}, fmt.Errorf("classifiers.yaml: unknown classifier ID %q", classifierID)
	}
	if err := validateClassifier(*selected); err != nil {
		return ClassifierConfig{}, err
	}
	return *selected, nil
}

func missingClassifierField(entry ClassifierConfig) string {
	for _, field := range []struct{ name, value string }{
		{"id", entry.ID},
		{"provider", entry.Provider},
		{"model", entry.Model},
		{"endpoint", entry.Endpoint},
	} {
		if strings.TrimSpace(field.value) == "" {
			return field.name
		}
	}
	return ""
}

func validateClassifier(config ClassifierConfig) error {
	if config.Provider != classifierProviderTypeSafe && config.Provider != classifierProviderLaya {
		return fmt.Errorf("classifier %q uses provider %q; supported providers are typesafe and laya, other entries are catalog candidates", config.ID, config.Provider)
	}
	endpoint, err := url.Parse(config.Endpoint)
	if err != nil || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.Hostname() == "" ||
		endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return fmt.Errorf("classifier %q endpoint must be an HTTP URL without credentials, query, or fragment", config.ID)
	}
	if config.Provider == classifierProviderTypeSafe && endpoint.Scheme != "https" {
		return fmt.Errorf("classifier %q sends an API key and requires an HTTPS endpoint", config.ID)
	}
	if config.Provider == classifierProviderLaya && !isLoopbackHost(endpoint.Hostname()) {
		return fmt.Errorf("classifier %q requires a loopback endpoint", config.ID)
	}
	return nil
}

func isLoopbackHost(host string) bool {
	return host == "localhost" || host == "127.0.0.1" || host == "::1"
}
