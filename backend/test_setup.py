from services.supabase_client import supabase, supabase_admin
from graph.graph_builder import build_graph
from dotenv import load_dotenv

load_dotenv()

print("\n── Testing Supabase connection ──")
try:
    result = supabase.table("sessions").select("id").limit(1).execute()
    print("✅ Supabase anon client connected")
except Exception as e:
    print(f"❌ Supabase failed: {e}")

print("\n── Testing LangGraph ──")
try:
    app = build_graph()
    print("✅ LangGraph graph built successfully")
except Exception as e:
    print(f"❌ LangGraph failed: {e}")

print("\n── All checks done ──")