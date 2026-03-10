from graph.graph_builder import build_graph
from tools.csv_parser import parse_csv
from langchain_core.messages import HumanMessage

app = build_graph()
transactions = parse_csv("data/sample_transactions.csv")

def run(user_message: str, include_transactions: bool = True):
    print(f"\n{'='*50}")
    print(f"User: {user_message}")
    
    initial_state = {
        "messages": [HumanMessage(content=user_message)],
        "transactions": transactions if include_transactions else [],
        "budget_summary": {},
        "savings_tips": [],
        "user_goal": "save 10000 next month",
        "next_agent": "",
        "final_response": ""
    }
    
    result = app.invoke(initial_state)
    print(f"\nAgent Response:")
    print(result["messages"][-1].content)

# Test 3 different intents — watch orchestrator route to different agents each time
# run("Categorize my spending please")          # → expense_tracker
# run("Am I overspending this month?")          # → budget_analyst  
run("I feel like I'm always broke somehow")   # → financial_coach