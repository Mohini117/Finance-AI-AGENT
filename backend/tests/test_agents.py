"""
tests/test_agents.py
====================
Full deploy-readiness test suite for the MultiAgent Finance AI system.

AGENTS TESTED:
  - guardrails          (safety layer)
  - input_validation    (pre-routing checks)
  - orchestrator        (routing logic)
  - expense_tracker     (categorization + breakdown)
  - budget_analyst      (math accuracy + projection)
  - savings_finder      (search query + Tavily fallback)
  - financial_coach     (goal/no-goal mode + tone)
  - anonymizer          (PII stripping + math)

RUN:
  pytest tests/test_agents.py -v
  pytest tests/test_agents.py -v -k "Guard"
  pytest tests/test_agents.py -v -k "Integration"
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("TAVILY_API_KEY", "test-key")

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.budget_analyst import budget_analyst_agent
from agents.expense_tracker import expense_tracker_agent
from agents.financial_coach import financial_coach_agent
from agents.guardrails import guardrails_agent
from agents.input_validation import input_validation_agent
from agents.orchestrator import orchestrator_agent
from agents.savings_finder import savings_finder_agent
from tools.anonymizer import anonymize_transactions, summarize_locally

SAMPLE_TRANSACTIONS = [
    {"amount": 500,  "category": "Uncategorized", "description": "Swiggy Order",     "date": "2024-01-01"},
    {"amount": 390,  "category": "Uncategorized", "description": "Zomato Order",     "date": "2024-01-02"},
    {"amount": 2100, "category": "Uncategorized", "description": "Amazon Shopping",  "date": "2024-01-03"},
    {"amount": 1800, "category": "Uncategorized", "description": "Flipkart Shopping","date": "2024-01-04"},
    {"amount": 1100, "category": "Uncategorized", "description": "Big Bazaar",       "date": "2024-01-05"},
    {"amount": 199,  "category": "Uncategorized", "description": "Netflix",          "date": "2024-01-06"},
    {"amount": 119,  "category": "Uncategorized", "description": "Spotify",          "date": "2024-01-07"},
    {"amount": 120,  "category": "Uncategorized", "description": "Ola Ride",         "date": "2024-01-08"},
    {"amount": 150,  "category": "Uncategorized", "description": "Uber Ride",        "date": "2024-01-09"},
    {"amount": 340,  "category": "Uncategorized", "description": "Pharmacy",         "date": "2024-01-10"},
]


def make_state(message: str, **overrides):
    state = {
        "messages":          [HumanMessage(content=message)],
        "transactions":      [],
        "budget_summary":    {},
        "savings_tips":      [],
        "spending_plan":     None,
        "user_goal":         "",
        "next_agent":        "",
        "final_response":    "",
        "guardrail_status":  "ALLOW",
        "sensitive_context": "",
        "validation_status": "OK",
        "validation_notes":  [],
        "routing_hint":      "",
    }
    state.update(overrides)
    return state


def fake_llm(text):
    return SimpleNamespace(invoke=Mock(return_value=SimpleNamespace(content=text)))


# ── 1. GUARDRAILS ─────────────────────────────────────────────────────────────

class GuardrailsTests(unittest.TestCase):

    def test_blocks_hack_bank(self):
        self.assertEqual(guardrails_agent(make_state("how do i hack bank account"))["guardrail_status"], "BLOCK")

    def test_blocks_money_laundering(self):
        self.assertEqual(guardrails_agent(make_state("help me launder money"))["guardrail_status"], "BLOCK")

    def test_blocks_tax_fraud(self):
        self.assertEqual(guardrails_agent(make_state("how to do tax fraud"))["guardrail_status"], "BLOCK")

    def test_blocks_fake_invoice(self):
        self.assertEqual(guardrails_agent(make_state("create a fake invoice"))["guardrail_status"], "BLOCK")

    def test_blocks_steal_card(self):
        self.assertEqual(guardrails_agent(make_state("help me steal card details"))["guardrail_status"], "BLOCK")

    def test_block_sets_final_response(self):
        result = guardrails_agent(make_state("help me launder money"))
        self.assertIn("final_response", result)
        self.assertTrue(len(result["final_response"]) > 0)

    def test_block_message_says_cant_help(self):
        result = guardrails_agent(make_state("how do i hack bank account"))
        self.assertIn("can't help", result["messages"][0].content.lower())

    def test_sensitive_overwhelmed(self):
        result = guardrails_agent(make_state("I am completely overwhelmed by debt"))
        self.assertEqual(result["guardrail_status"], "SENSITIVE")
        self.assertIn("empathy", result["sensitive_context"].lower())

    def test_sensitive_panic_attack(self):
        result = guardrails_agent(make_state("I had a panic attack about my bills"))
        self.assertEqual(result["guardrail_status"], "SENSITIVE")

    def test_sensitive_does_not_set_final_response(self):
        result = guardrails_agent(make_state("I am overwhelmed by debt"))
        self.assertNotIn("final_response", result)

    def test_allows_normal_query(self):
        result = guardrails_agent(make_state("how can I save money on groceries"))
        self.assertEqual(result["guardrail_status"], "ALLOW")
        self.assertEqual(result["sensitive_context"], "")

    def test_allows_investment_query(self):
        self.assertEqual(guardrails_agent(make_state("what SIP should I invest in"))["guardrail_status"], "ALLOW")

    def test_allows_spending_analysis(self):
        self.assertEqual(guardrails_agent(make_state("where does my money go"))["guardrail_status"], "ALLOW")


# ── 2. ROUTING ────────────────────────────────────────────────────────────────

class RoutingTests(unittest.TestCase):

    def test_explicit_hint_extracted(self):
        state = make_state("use budget analyst", transactions=SAMPLE_TRANSACTIONS)
        self.assertEqual(input_validation_agent(state)["routing_hint"], "budget_analyst")

    def test_missing_goal_is_soft_warning(self):
        result = input_validation_agent(make_state("analyze", transactions=SAMPLE_TRANSACTIONS))
        self.assertEqual(result["validation_status"], "OK")
        self.assertIn("missing_goal", result["validation_notes"])

    def test_no_transactions_blocks_analysis(self):
        result = input_validation_agent(make_state("categorize my transactions", transactions=[]))
        self.assertEqual(result["validation_status"], "BLOCK")
        self.assertIn("missing_transactions", result["validation_notes"])

    def test_validation_precomputes_budget_summary(self):
        result = input_validation_agent(make_state("analyze", transactions=SAMPLE_TRANSACTIONS))
        self.assertIn("total_spent", result["budget_summary"])

    def test_goal_set_no_missing_goal_note(self):
        result = input_validation_agent(make_state("analyze", transactions=SAMPLE_TRANSACTIONS, user_goal="Save Rs 50k"))
        self.assertNotIn("missing_goal", result.get("validation_notes", []))

    def test_orchestrator_uses_hint_no_llm(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("LLM must not be called")))
        with patch("agents.orchestrator.llm", no_llm):
            result = orchestrator_agent(make_state("anything", routing_hint="expense_tracker"))
        self.assertEqual(result["next_agent"], "expense_tracker")

    def test_orchestrator_greeting_to_coach(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            self.assertEqual(orchestrator_agent(make_state("hello there"))["next_agent"], "financial_coach")

    def test_orchestrator_where_money_goes_to_expense_tracker(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            self.assertEqual(orchestrator_agent(make_state("where does my money go"))["next_agent"], "expense_tracker")

    def test_orchestrator_categorize_to_expense_tracker(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            self.assertEqual(orchestrator_agent(make_state("categorize my transactions"))["next_agent"], "expense_tracker")

    def test_orchestrator_save_expenses_to_savings_finder(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            # "save my expenses" is now in heuristic keywords
            self.assertEqual(orchestrator_agent(make_state("how do i save my expenses"))["next_agent"], "savings_finder")

    def test_orchestrator_invest_to_financial_coach(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            self.assertEqual(orchestrator_agent(make_state("how do i start investing in SIP"))["next_agent"], "financial_coach")

    def test_orchestrator_cut_down_to_savings_finder(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            self.assertEqual(orchestrator_agent(make_state("how to cut down my spending"))["next_agent"], "savings_finder")


# ── 3. ANONYMIZER ─────────────────────────────────────────────────────────────

class AnonymizerTests(unittest.TestCase):

    def test_strips_phone_number(self):
        # Note: the REF regex (9+ digits) fires before PHONE (10 digits exactly),
        # so 10-digit numbers are replaced with [REF]. Either way the number is gone.
        result = anonymize_transactions([{"description": "Transfer to 9876543210", "amount": 500, "date": "2024-01-01"}])
        self.assertNotIn("9876543210", result[0]["description"])
        # Should be replaced by either [REF] or [PHONE]
        self.assertTrue("[REF]" in result[0]["description"] or "[PHONE]" in result[0]["description"])

    def test_strips_email(self):
        result = anonymize_transactions([{"description": "From user@example.com", "amount": 200, "date": "2024-01-01"}])
        self.assertIn("[EMAIL]", result[0]["description"])

    def test_strips_long_ref_number(self):
        result = anonymize_transactions([{"description": "UPI ref 123456789012", "amount": 100, "date": "2024-01-01"}])
        self.assertNotIn("123456789012", result[0]["description"])

    def test_preserves_bigbazaar_merchant_name(self):
        result = anonymize_transactions([{"description": "BIGBAZAAR PURCHASE", "amount": 1000, "date": "2024-01-01", "category": "Groceries"}])
        self.assertNotIn("[ID]", result[0]["description"])
        self.assertIn("BIGBAZAAR", result[0]["description"])

    def test_preserves_amazon_merchant_name(self):
        result = anonymize_transactions([{"description": "AMAZONPAYMENTS", "amount": 500, "date": "2024-01-01", "category": "Shopping"}])
        self.assertNotIn("[ID]", result[0]["description"])

    def test_strips_real_alphanumeric_id(self):
        result = anonymize_transactions([{"description": "TXN4829301XYZ payment", "amount": 100, "date": "2024-01-01"}])
        self.assertIn("[ID]", result[0]["description"])

    def test_preserves_existing_real_category(self):
        result = anonymize_transactions([{"description": "Swiggy", "amount": 300, "category": "Food", "date": "2024-01-01"}])
        self.assertEqual(result[0]["category"], "Food")

    def test_none_amount_defaults_to_zero(self):
        result = anonymize_transactions([{"description": "Purchase", "amount": None, "date": "2024-01-01"}])
        self.assertEqual(result[0]["amount"], 0.0)

    def test_summarize_total_correct(self):
        result = summarize_locally(SAMPLE_TRANSACTIONS)
        self.assertAlmostEqual(result["total_spent"], sum(t["amount"] for t in SAMPLE_TRANSACTIONS), places=1)

    def test_summarize_count_correct(self):
        self.assertEqual(summarize_locally(SAMPLE_TRANSACTIONS)["transaction_count"], 10)

    def test_summarize_max_correct(self):
        self.assertEqual(summarize_locally(SAMPLE_TRANSACTIONS)["max_single_spend"], 2100)

    def test_summarize_min_correct(self):
        self.assertEqual(summarize_locally(SAMPLE_TRANSACTIONS)["min_single_spend"], 119)

    def test_summarize_uses_actual_date_range(self):
        result = summarize_locally(SAMPLE_TRANSACTIONS)
        total = sum(t["amount"] for t in SAMPLE_TRANSACTIONS)
        self.assertAlmostEqual(result["daily_avg"], round(total / 9, 2), places=1)

    def test_summarize_empty_returns_empty_dict(self):
        self.assertEqual(summarize_locally([]), {})


# ── 4. EXPENSE TRACKER ────────────────────────────────────────────────────────

class ExpenseTrackerTests(unittest.TestCase):

    def test_correct_agent_name(self):
        with patch("agents.expense_tracker.llm", fake_llm("output")):
            result = expense_tracker_agent(make_state("analyze", transactions=SAMPLE_TRANSACTIONS))
        self.assertEqual(result["messages"][0].name, "expense_tracker")

    def test_no_transactions_returns_upload_prompt(self):
        result = expense_tracker_agent(make_state("analyze", transactions=[]))
        self.assertIn("No Transactions", result["messages"][0].content)

    def _get_prompt(self, transactions):
        captured = []
        def capture(msgs):
            captured.append(msgs[0].content)
            return SimpleNamespace(content="ok")
        with patch("agents.expense_tracker.llm", SimpleNamespace(invoke=capture)):
            expense_tracker_agent(make_state("analyze", transactions=transactions))
        return captured[0] if captured else ""

    def test_swiggy_resolved_to_food(self):
        prompt = self._get_prompt([{"amount": 500, "category": "Uncategorized", "description": "Swiggy Order", "date": "2024-01-01"}])
        self.assertIn("Food", prompt)

    def test_zomato_resolved_to_food(self):
        prompt = self._get_prompt([{"amount": 400, "category": "Uncategorized", "description": "Zomato Order", "date": "2024-01-01"}])
        self.assertIn("Food", prompt)

    def test_amazon_resolved_to_shopping(self):
        prompt = self._get_prompt([{"amount": 2000, "category": "Uncategorized", "description": "Amazon Shopping", "date": "2024-01-01"}])
        self.assertIn("Shopping", prompt)

    def test_flipkart_resolved_to_shopping(self):
        prompt = self._get_prompt([{"amount": 1500, "category": "Uncategorized", "description": "Flipkart Shopping", "date": "2024-01-01"}])
        self.assertIn("Shopping", prompt)

    def test_big_bazaar_resolved_to_groceries(self):
        prompt = self._get_prompt([{"amount": 1000, "category": "Uncategorized", "description": "Big Bazaar", "date": "2024-01-01"}])
        self.assertIn("Groceries", prompt)

    def test_ola_resolved_to_transport(self):
        prompt = self._get_prompt([{"amount": 120, "category": "Uncategorized", "description": "Ola Ride", "date": "2024-01-01"}])
        self.assertIn("Transport", prompt)

    def test_netflix_resolved_to_subscriptions(self):
        prompt = self._get_prompt([{"amount": 199, "category": "Uncategorized", "description": "Netflix", "date": "2024-01-01"}])
        self.assertIn("Subscriptions", prompt)

    def test_pharmacy_resolved_to_health(self):
        prompt = self._get_prompt([{"amount": 340, "category": "Uncategorized", "description": "Pharmacy", "date": "2024-01-01"}])
        self.assertIn("Health", prompt)

    def test_existing_category_preserved(self):
        prompt = self._get_prompt([{"amount": 500, "category": "Entertainment", "description": "Cinema", "date": "2024-01-01"}])
        self.assertIn("Entertainment", prompt)

    def test_prompt_has_precomputed_total(self):
        prompt = self._get_prompt(SAMPLE_TRANSACTIONS)
        # Total is 6818, formatted as "6,818" in the prompt with comma separator
        total = sum(t["amount"] for t in SAMPLE_TRANSACTIONS)
        formatted = f"{total:,.0f}"  # "6,818"
        self.assertIn(formatted, prompt)

    def test_uncategorized_not_in_breakdown(self):
        prompt = self._get_prompt(SAMPLE_TRANSACTIONS)
        if "Category breakdown" in prompt:
            section = prompt.split("Category breakdown")[1].split("Top 3")[0]
            self.assertNotIn("Uncategorized", section)


# ── 5. BUDGET ANALYST ─────────────────────────────────────────────────────────

class BudgetAnalystTests(unittest.TestCase):

    def test_correct_agent_name(self):
        with patch("agents.budget_analyst.llm", fake_llm("output")):
            result = budget_analyst_agent(make_state("budget", transactions=SAMPLE_TRANSACTIONS))
        self.assertEqual(result["messages"][0].name, "budget_analyst")

    def test_no_transactions_returns_no_data(self):
        result = budget_analyst_agent(make_state("budget", transactions=[]))
        self.assertIn("No Data", result["messages"][0].content)

    def test_budget_summary_in_output(self):
        with patch("agents.budget_analyst.llm", fake_llm("output")):
            result = budget_analyst_agent(make_state("budget", transactions=SAMPLE_TRANSACTIONS))
        self.assertIn("budget_summary", result)
        self.assertIn("total_spent", result["budget_summary"])

    def test_budget_summary_total_accurate(self):
        with patch("agents.budget_analyst.llm", fake_llm("output")):
            result = budget_analyst_agent(make_state("budget", transactions=SAMPLE_TRANSACTIONS))
        self.assertEqual(result["budget_summary"]["total_spent"], sum(t["amount"] for t in SAMPLE_TRANSACTIONS))

    def test_budget_summary_has_daily_avg(self):
        with patch("agents.budget_analyst.llm", fake_llm("output")):
            result = budget_analyst_agent(make_state("budget", transactions=SAMPLE_TRANSACTIONS))
        self.assertGreater(result["budget_summary"]["daily_avg"], 0)

    def test_budget_summary_flows_to_financial_coach(self):
        with patch("agents.budget_analyst.llm", fake_llm("output")):
            ba_result = budget_analyst_agent(make_state("budget", transactions=SAMPLE_TRANSACTIONS))
        state = {**make_state("how to improve", transactions=SAMPLE_TRANSACTIONS), **ba_result}
        with patch("agents.financial_coach.llm", fake_llm("Advice")):
            coach = financial_coach_agent(state)
        self.assertEqual(coach["messages"][0].name, "financial_coach")


# ── 6. SAVINGS FINDER ────────────────────────────────────────────────────────

class SavingsFinderTests(unittest.TestCase):

    def _run_with_mocks(self, query_response, tips_response, tavily_results=None, tavily_error=False):
        mock_llm = Mock()
        mock_llm.invoke = Mock(side_effect=[
            SimpleNamespace(content=query_response),
            SimpleNamespace(content=tips_response),
        ])
        if tavily_error:
            tavily_mock = SimpleNamespace(search=Mock(side_effect=Exception("Network error")))
        elif tavily_results is None:
            tavily_mock = None
        else:
            tavily_mock = SimpleNamespace(search=Mock(return_value={"results": tavily_results}))

        with patch("agents.savings_finder.llm", mock_llm), \
             patch("agents.savings_finder.tavily", tavily_mock):
            return savings_finder_agent(make_state("save money", transactions=SAMPLE_TRANSACTIONS))

    def test_correct_agent_name(self):
        result = self._run_with_mocks('"food India 2024"', "---\nTip 1\n---\nTip 2\n---\nTip 3",
                                      [{"title": "t", "content": "c"}])
        self.assertEqual(result["messages"][0].name, "savings_finder")

    def test_returns_3_tips(self):
        result = self._run_with_mocks('"food India 2024"', "---\nTip 1\n---\nTip 2\n---\nTip 3",
                                      [{"title": "t", "content": "c"}])
        self.assertGreaterEqual(len(result["savings_tips"]), 3)

    def test_works_without_tavily(self):
        result = self._run_with_mocks('"food India 2024"', "---\nTip 1\n---\nTip 2\n---\nTip 3", tavily_results=None)
        self.assertEqual(result["messages"][0].name, "savings_finder")

    def test_handles_tavily_exception(self):
        result = self._run_with_mocks('"food India 2024"', "---\nTip 1\n---\nTip 2\n---\nTip 3", tavily_error=True)
        self.assertEqual(result["messages"][0].name, "savings_finder")

    def test_tips_stored_in_state(self):
        result = self._run_with_mocks('"food India 2024"', "---\nTip 1\n---\nTip 2\n---\nTip 3")
        self.assertIsInstance(result["savings_tips"], list)

    def test_query_prompt_references_top_category(self):
        mock_llm = Mock()
        mock_llm.invoke = Mock(side_effect=[
            SimpleNamespace(content='"food delivery India 2024"'),
            SimpleNamespace(content="---\nTip 1\n---\nTip 2\n---\nTip 3"),
        ])
        with patch("agents.savings_finder.llm", mock_llm), patch("agents.savings_finder.tavily", None):
            savings_finder_agent(make_state("save money", transactions=SAMPLE_TRANSACTIONS))
        first_prompt = mock_llm.invoke.call_args_list[0][0][0][0].content
        self.assertIn("biggest spending category", first_prompt.lower())


# ── 7. FINANCIAL COACH ────────────────────────────────────────────────────────

class FinancialCoachTests(unittest.TestCase):

    def test_greeting_no_llm_call(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("LLM must not be called")))
        with patch("agents.financial_coach.llm", no_llm):
            result = financial_coach_agent(make_state("hello"))
        # Greeting response contains "personal finance coach"
        self.assertIn("personal finance coach", result["messages"][0].content.lower())

    def test_greeting_no_transaction_data_leaked(self):
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("LLM must not be called")))
        with patch("agents.financial_coach.llm", no_llm):
            result = financial_coach_agent(make_state("hello", transactions=SAMPLE_TRANSACTIONS))
        self.assertNotIn("transactions loaded", result["messages"][0].content.lower())

    def test_finance_query_calls_llm(self):
        with patch("agents.financial_coach.llm", fake_llm("SIP advice")):
            result = financial_coach_agent(make_state("how do i invest", transactions=SAMPLE_TRANSACTIONS))
        self.assertEqual(result["messages"][0].content, "SIP advice")
        self.assertEqual(result["messages"][0].name, "financial_coach")

    def test_no_goal_prompt_says_one_question(self):
        captured = []
        def capture(msgs):
            captured.append(msgs[0].content)
            return SimpleNamespace(content="What is your goal?")
        with patch("agents.financial_coach.llm", SimpleNamespace(invoke=capture)):
            financial_coach_agent(make_state("how do i invest", transactions=SAMPLE_TRANSACTIONS, validation_notes=["missing_goal"]))
        self.assertIn("ONE question", captured[0])
        self.assertIn("Do NOT give tips", captured[0])

    def test_goal_set_prompt_has_3_bullets(self):
        captured = []
        def capture(msgs):
            captured.append(msgs[0].content)
            return SimpleNamespace(content="Invest Rs 2000")
        with patch("agents.financial_coach.llm", SimpleNamespace(invoke=capture)):
            financial_coach_agent(make_state("how do i invest", transactions=SAMPLE_TRANSACTIONS, user_goal="Save Rs 50k in 6 months"))
        self.assertIn("3 bullets", captured[0])

    def test_sensitive_context_triggers_empathy(self):
        captured = []
        def capture(msgs):
            captured.append(msgs[0].content)
            return SimpleNamespace(content="Here is help")
        with patch("agents.financial_coach.llm", SimpleNamespace(invoke=capture)):
            financial_coach_agent(make_state(
                "I am overwhelmed",
                sensitive_context="User may be emotionally stressed. Lead with empathy.",
                transactions=SAMPLE_TRANSACTIONS,
            ))
        self.assertIn("empathetic", captured[0].lower())

    def test_final_response_in_state(self):
        with patch("agents.financial_coach.llm", fake_llm("Start a SIP today")):
            result = financial_coach_agent(make_state("how to invest", user_goal="Save Rs 1 lakh"))
        self.assertEqual(result["final_response"], "Start a SIP today")

    def test_response_is_ai_message(self):
        with patch("agents.financial_coach.llm", fake_llm("advice")):
            result = financial_coach_agent(make_state("how to save money"))
        self.assertIsInstance(result["messages"][0], AIMessage)


# ── 8. INTEGRATION ────────────────────────────────────────────────────────────

class IntegrationTests(unittest.TestCase):

    def test_block_sets_final_response_and_stops(self):
        result = guardrails_agent(make_state("how to hack bank account"))
        self.assertEqual(result["guardrail_status"], "BLOCK")
        self.assertTrue(len(result["final_response"]) > 0)

    def test_full_flow_where_money_goes(self):
        state = make_state("where does my money go", transactions=SAMPLE_TRANSACTIONS)
        state = {**state, **input_validation_agent(state)}
        self.assertEqual(state["validation_status"], "OK")
        state = {**state, **guardrails_agent(state)}
        self.assertEqual(state["guardrail_status"], "ALLOW")
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            state = {**state, **orchestrator_agent(state)}
        self.assertEqual(state["next_agent"], "expense_tracker")
        with patch("agents.expense_tracker.llm", fake_llm("## 📊 Spending Breakdown\nFood: ₹890")):
            result = expense_tracker_agent(state)
        self.assertEqual(result["messages"][0].name, "expense_tracker")
        self.assertIn("Spending Breakdown", result["messages"][0].content)

    def test_full_flow_save_expenses(self):
        state = make_state("how do i save my expenses", transactions=SAMPLE_TRANSACTIONS)
        state = {**state, **input_validation_agent(state)}
        state = {**state, **guardrails_agent(state)}
        no_llm = SimpleNamespace(invoke=Mock(side_effect=AssertionError("no LLM")))
        with patch("agents.orchestrator.llm", no_llm):
            result = orchestrator_agent(state)
        self.assertEqual(result["next_agent"], "savings_finder")

    def test_sensitive_context_flows_to_coach(self):
        state = make_state("I am overwhelmed by debt", transactions=SAMPLE_TRANSACTIONS)
        state = {**state, **guardrails_agent(state)}
        self.assertEqual(state["guardrail_status"], "SENSITIVE")
        captured = []
        def capture(msgs):
            captured.append(msgs[0].content)
            return SimpleNamespace(content="help")
        with patch("agents.financial_coach.llm", SimpleNamespace(invoke=capture)):
            financial_coach_agent(state)
        self.assertIn("empathetic", captured[0].lower())

    def test_budget_summary_from_validation_flows_to_coach(self):
        state = make_state("how to improve finances", transactions=SAMPLE_TRANSACTIONS)
        state = {**state, **input_validation_agent(state)}
        self.assertGreater(state["budget_summary"].get("total_spent", 0), 0)
        with patch("agents.financial_coach.llm", fake_llm("Here is advice")):
            result = financial_coach_agent(state)
        self.assertEqual(result["messages"][0].name, "financial_coach")

    def test_all_specialist_agents_return_ai_message(self):
        agents_under_test = [
            (expense_tracker_agent, make_state("analyze", transactions=SAMPLE_TRANSACTIONS), "agents.expense_tracker.llm"),
            (budget_analyst_agent,  make_state("budget",  transactions=SAMPLE_TRANSACTIONS), "agents.budget_analyst.llm"),
            (financial_coach_agent, make_state("hello"),                                     "agents.financial_coach.llm"),
        ]
        for fn, state, patch_path in agents_under_test:
            with self.subTest(agent=fn.__name__):
                with patch(patch_path, fake_llm("response")):
                    result = fn(state)
                self.assertIsInstance(result["messages"][0], AIMessage)


if __name__ == "__main__":
    unittest.main(verbosity=2)