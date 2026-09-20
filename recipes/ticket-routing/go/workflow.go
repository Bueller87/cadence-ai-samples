package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"go.uber.org/cadence"
	"go.uber.org/cadence/workflow"
)

const (
	TaskList = "ticket-routing"

	StatusAssigned   = "ASSIGNED"
	StatusUnroutable = "UNROUTABLE"

	minimumDepartmentConfidence = 0.65
)

type Department string

const (
	DepartmentBilling   Department = "billing"
	DepartmentTechnical Department = "technical"
	DepartmentAccount   Department = "account"
	DepartmentContent   Department = "content"
)

type Priority string

const (
	PriorityLow      Priority = "low"
	PriorityNormal   Priority = "normal"
	PriorityHigh     Priority = "high"
	PriorityCritical Priority = "critical"
)

type Complexity string

const (
	ComplexityTier1 Complexity = "tier1"
	ComplexityTier2 Complexity = "tier2"
	ComplexityTier3 Complexity = "tier3"
)

// Ticket is the input to one TicketIntakeWorkflow execution.
type Ticket struct {
	TicketID string `json:"ticket_id"`
	Message  string `json:"message"`
}

// RoutingDecision is the typed result returned by the classification Activity.
type RoutingDecision struct {
	Department           Department `json:"department"`
	Priority             Priority   `json:"priority"`
	Complexity           Complexity `json:"complexity"`
	DepartmentConfidence float64    `json:"department_confidence"`
	PriorityConfidence   float64    `json:"priority_confidence"`
	ComplexityConfidence float64    `json:"complexity_confidence"`
	Model                string     `json:"model"`
}

// ClassifiedTicket is passed from the parent to a department Child Workflow.
type ClassifiedTicket struct {
	Ticket         Ticket          `json:"ticket"`
	Classification RoutingDecision `json:"classification"`
}

// AssignmentResult is returned by a department Child Workflow.
type AssignmentResult struct {
	TicketID   string     `json:"ticket_id"`
	Department Department `json:"department"`
	EmployeeID string     `json:"employee_id"`
	Status     string     `json:"status"`
}

// TicketResult is the completed Phase 1 result returned by the parent Workflow.
type TicketResult struct {
	TicketID        string          `json:"ticket_id"`
	Classification  RoutingDecision `json:"classification"`
	Department      Department      `json:"department,omitempty"`
	EmployeeID      string          `json:"employee_id,omitempty"`
	ChildWorkflowID string          `json:"child_workflow_id,omitempty"`
	Status          string          `json:"status"`
	Reason          string          `json:"reason,omitempty"`
}

// TicketIntakeWorkflow classifies one ticket and routes it to one department.
func TicketIntakeWorkflow(ctx workflow.Context, ticket Ticket) (TicketResult, error) {
	activityOptions := workflow.ActivityOptions{
		ScheduleToStartTimeout: time.Minute,
		StartToCloseTimeout:    10 * time.Second,
		ScheduleToCloseTimeout: 30 * time.Second,
		RetryPolicy: &cadence.RetryPolicy{
			InitialInterval:    time.Second,
			BackoffCoefficient: 2,
			MaximumInterval:    5 * time.Second,
			ExpirationInterval: 30 * time.Second,
		},
	}
	ctx = workflow.WithActivityOptions(ctx, activityOptions)

	var decision RoutingDecision
	if err := workflow.ExecuteActivity(ctx, ClassifyTicket, ticket).Get(ctx, &decision); err != nil {
		return TicketResult{}, fmt.Errorf("classify ticket: %w", err)
	}

	if reason := invalidClassificationReason(decision); reason != "" {
		return TicketResult{
			TicketID:       ticket.TicketID,
			Classification: decision,
			Status:         StatusUnroutable,
			Reason:         reason,
		}, nil
	}

	classified := ClassifiedTicket{Ticket: ticket, Classification: decision}
	childID := childWorkflowID(ticket.TicketID, decision.Department)
	childContext := workflow.WithChildOptions(ctx, workflow.ChildWorkflowOptions{
		WorkflowID:                   childID,
		ExecutionStartToCloseTimeout: time.Minute,
		TaskStartToCloseTimeout:      10 * time.Second,
	})

	var childWorkflow interface{}
	switch decision.Department {
	case DepartmentBilling:
		childWorkflow = BillingWorkflow
	case DepartmentTechnical:
		childWorkflow = TechnicalWorkflow
	case DepartmentAccount:
		childWorkflow = AccountWorkflow
	case DepartmentContent:
		childWorkflow = ContentWorkflow
	default:
		return TicketResult{}, fmt.Errorf("unsupported department %q", decision.Department)
	}

	var assignment AssignmentResult
	if err := workflow.ExecuteChildWorkflow(childContext, childWorkflow, classified).Get(childContext, &assignment); err != nil {
		return TicketResult{}, fmt.Errorf("run %s child workflow: %w", decision.Department, err)
	}

	return TicketResult{
		TicketID:        ticket.TicketID,
		Classification:  decision,
		Department:      assignment.Department,
		EmployeeID:      assignment.EmployeeID,
		ChildWorkflowID: childID,
		Status:          assignment.Status,
	}, nil
}

// ClassifyTicket is a deterministic mock Jev classifier. A later phase can
// replace this Activity implementation without changing Workflow logic.
func ClassifyTicket(_ context.Context, ticket Ticket) (RoutingDecision, error) {
	if strings.TrimSpace(ticket.TicketID) == "" {
		return RoutingDecision{}, fmt.Errorf("ticket ID is required")
	}
	if strings.TrimSpace(ticket.Message) == "" {
		return RoutingDecision{}, fmt.Errorf("ticket message is required")
	}

	message := strings.ToLower(ticket.Message)
	decision := RoutingDecision{
		Department:           classifyDepartment(message),
		Priority:             classifyPriority(message),
		Complexity:           classifyComplexity(message),
		DepartmentConfidence: 0.92,
		PriorityConfidence:   0.88,
		ComplexityConfidence: 0.84,
		Model:                "mock-jev-v1",
	}

	return decision, nil
}

func classifyDepartment(message string) Department {
	switch {
	case containsAny(message, "bill", "charge", "payment", "refund", "invoice"):
		return DepartmentBilling
	case containsAny(message, "password", "sign in", "login", "email address", "profile", "account"):
		return DepartmentAccount
	case containsAny(message, "subtitle", "caption", "episode", "show", "movie", "audio", "catalog"):
		return DepartmentContent
	default:
		return DepartmentTechnical
	}
}

func classifyPriority(message string) Priority {
	switch {
	case containsAny(message, "security", "hacked", "charged many times", "service is down"):
		return PriorityCritical
	case containsAny(message, "urgent", "can't watch", "cannot watch", "locked out", "charged twice"):
		return PriorityHigh
	case containsAny(message, "when possible", "suggestion", "feature request"):
		return PriorityLow
	default:
		return PriorityNormal
	}
}

func classifyComplexity(message string) Complexity {
	switch {
	case containsAny(message, "every device", "all devices", "data loss", "charged many times"):
		return ComplexityTier3
	case containsAny(message, "multiple", "charged twice", "keeps crashing", "intermittent"):
		return ComplexityTier2
	default:
		return ComplexityTier1
	}
}

func containsAny(message string, values ...string) bool {
	for _, value := range values {
		if strings.Contains(message, value) {
			return true
		}
	}
	return false
}

func invalidClassificationReason(decision RoutingDecision) string {
	if !validDepartment(decision.Department) {
		return fmt.Sprintf("unsupported department %q", decision.Department)
	}
	if !validPriority(decision.Priority) {
		return fmt.Sprintf("unsupported priority %q", decision.Priority)
	}
	if !validComplexity(decision.Complexity) {
		return fmt.Sprintf("unsupported complexity %q", decision.Complexity)
	}
	if decision.DepartmentConfidence < minimumDepartmentConfidence {
		return fmt.Sprintf("department confidence %.2f is below %.2f", decision.DepartmentConfidence, minimumDepartmentConfidence)
	}
	return ""
}

func validDepartment(value Department) bool {
	return value == DepartmentBilling || value == DepartmentTechnical || value == DepartmentAccount || value == DepartmentContent
}

func validPriority(value Priority) bool {
	return value == PriorityLow || value == PriorityNormal || value == PriorityHigh || value == PriorityCritical
}

func validComplexity(value Complexity) bool {
	return value == ComplexityTier1 || value == ComplexityTier2 || value == ComplexityTier3
}

func childWorkflowID(ticketID string, department Department) string {
	return fmt.Sprintf("ticket-routing-child-%s-%s", ticketID, department)
}

func BillingWorkflow(_ workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ticket, DepartmentBilling, "SW-BILLING-101")
}

func TechnicalWorkflow(_ workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ticket, DepartmentTechnical, "SW-TECH-202")
}

func AccountWorkflow(_ workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ticket, DepartmentAccount, "SW-ACCOUNT-303")
}

func ContentWorkflow(_ workflow.Context, ticket ClassifiedTicket) (AssignmentResult, error) {
	return assignTicket(ticket, DepartmentContent, "SW-CONTENT-404")
}

func assignTicket(ticket ClassifiedTicket, department Department, employeeID string) (AssignmentResult, error) {
	if ticket.Classification.Department != department {
		return AssignmentResult{}, fmt.Errorf("%s workflow received %s ticket", department, ticket.Classification.Department)
	}
	return AssignmentResult{
		TicketID:   ticket.Ticket.TicketID,
		Department: department,
		EmployeeID: employeeID,
		Status:     StatusAssigned,
	}, nil
}
