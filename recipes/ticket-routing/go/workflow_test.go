package main

import (
	"testing"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"
	"go.uber.org/cadence/testsuite"
)

func TestTicketIntakeWorkflowRoutesEveryDepartment(t *testing.T) {
	tests := []struct {
		name       string
		department Department
		employeeID string
	}{
		{name: "billing", department: DepartmentBilling, employeeID: "SW-BILLING-101"},
		{name: "technical", department: DepartmentTechnical, employeeID: "SW-TECH-202"},
		{name: "account", department: DepartmentAccount, employeeID: "SW-ACCOUNT-303"},
		{name: "content", department: DepartmentContent, employeeID: "SW-CONTENT-404"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			env := newWorkflowEnvironment(t)
			ticket := Ticket{TicketID: "route-" + test.name, Message: "synthetic test ticket"}
			decision := validDecision(test.department)
			env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()

			env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

			require.True(t, env.IsWorkflowCompleted())
			require.NoError(t, env.GetWorkflowError())
			var result TicketResult
			require.NoError(t, env.GetWorkflowResult(&result))
			require.Equal(t, test.department, result.Department)
			require.Equal(t, test.employeeID, result.EmployeeID)
			require.Equal(t, StatusAssigned, result.Status)
			require.Equal(t, childWorkflowID(ticket.TicketID, test.department), result.ChildWorkflowID)
			env.AssertExpectations(t)
		})
	}
}

func TestDepartmentChildWorkflows(t *testing.T) {
	tests := []struct {
		name       string
		workflow   interface{}
		department Department
		employeeID string
	}{
		{name: "billing", workflow: BillingWorkflow, department: DepartmentBilling, employeeID: "SW-BILLING-101"},
		{name: "technical", workflow: TechnicalWorkflow, department: DepartmentTechnical, employeeID: "SW-TECH-202"},
		{name: "account", workflow: AccountWorkflow, department: DepartmentAccount, employeeID: "SW-ACCOUNT-303"},
		{name: "content", workflow: ContentWorkflow, department: DepartmentContent, employeeID: "SW-CONTENT-404"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var suite testsuite.WorkflowTestSuite
			env := suite.NewTestWorkflowEnvironment()
			input := ClassifiedTicket{
				Ticket:         Ticket{TicketID: "child-" + test.name, Message: "synthetic test ticket"},
				Classification: validDecision(test.department),
			}

			env.ExecuteWorkflow(test.workflow, input)

			require.True(t, env.IsWorkflowCompleted())
			require.NoError(t, env.GetWorkflowError())
			var result AssignmentResult
			require.NoError(t, env.GetWorkflowResult(&result))
			require.Equal(t, input.Ticket.TicketID, result.TicketID)
			require.Equal(t, test.department, result.Department)
			require.Equal(t, test.employeeID, result.EmployeeID)
			require.Equal(t, StatusAssigned, result.Status)
		})
	}
}

func TestTicketIntakeWorkflowReturnsUnroutableForUnsupportedClassification(t *testing.T) {
	env := newWorkflowEnvironment(t)
	ticket := Ticket{TicketID: "invalid-001", Message: "synthetic test ticket"}
	decision := validDecision(Department("sales"))
	env.OnActivity(ClassifyTicket, mock.Anything, ticket).Return(decision, nil).Once()

	env.ExecuteWorkflow(TicketIntakeWorkflow, ticket)

	require.True(t, env.IsWorkflowCompleted())
	require.NoError(t, env.GetWorkflowError())
	var result TicketResult
	require.NoError(t, env.GetWorkflowResult(&result))
	require.Equal(t, StatusUnroutable, result.Status)
	require.Contains(t, result.Reason, "unsupported department")
	require.Empty(t, result.ChildWorkflowID)
	require.Empty(t, result.EmployeeID)
	env.AssertExpectations(t)
}

func TestMockClassifierProducesTypedDecision(t *testing.T) {
	decision, err := ClassifyTicket(t.Context(), Ticket{
		TicketID: "mock-001",
		Message:  "Urgent: I was charged twice for my subscription.",
	})

	require.NoError(t, err)
	require.Equal(t, DepartmentBilling, decision.Department)
	require.Equal(t, PriorityHigh, decision.Priority)
	require.Equal(t, ComplexityTier2, decision.Complexity)
	require.Equal(t, "mock-jev-v1", decision.Model)
}

func newWorkflowEnvironment(t *testing.T) *testsuite.TestWorkflowEnvironment {
	t.Helper()
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	env.RegisterWorkflow(TicketIntakeWorkflow)
	env.RegisterWorkflow(BillingWorkflow)
	env.RegisterWorkflow(TechnicalWorkflow)
	env.RegisterWorkflow(AccountWorkflow)
	env.RegisterWorkflow(ContentWorkflow)
	env.RegisterActivity(ClassifyTicket)
	return env
}

func validDecision(department Department) RoutingDecision {
	return RoutingDecision{
		Department:           department,
		Priority:             PriorityNormal,
		Complexity:           ComplexityTier1,
		DepartmentConfidence: 0.95,
		PriorityConfidence:   0.90,
		ComplexityConfidence: 0.85,
		Model:                "mock-jev-v1",
	}
}
