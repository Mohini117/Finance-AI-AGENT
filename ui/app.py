import streamlit as st
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.graph_builder import build_graph
from graph.chat_db import (
    init_db, create_session, get_all_sessions,
    save_message, get_messages, update_session_title,
    save_session_data, load_session_data, delete_session
)
from tools.csv_parser import parse_csv
from langchain_core.messages import HumanMessage
import tempfile
import time

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Finance Advisor AI",
    page_icon="💰",
    layout="wide"
)

# ─────────────────────────────────────────────
# INITIALIZE EVERYTHING ONCE
# ─────────────────────────────────────────────
init_db()

if "app" not in st.session_state:
    st.session_state.app = build_graph()

if "active_session_id" not in st.session_state:
    st.session_state.active_session_id = None

if "transactions" not in st.session_state:
    st.session_state.transactions = []

if "user_goal" not in st.session_state:
    st.session_state.user_goal = ""

if "quick_message" not in st.session_state:
    st.session_state.quick_message = None

if "rerun_needed" not in st.session_state:
    st.session_state.rerun_needed = False

# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.title("💰 Finance Advisor")
    
    st.divider()
    st.caption(
        "🔒 **Privacy:** Transaction details are "
        "anonymized before any processing. Raw data "
        "never leaves your device."
    )

    # ── New Chat Button ──
    if st.button("➕  New Chat", use_container_width=True, type="primary"):
        new_id = create_session("New Chat")
        st.session_state.active_session_id = new_id
        st.session_state.transactions = []
        st.session_state.user_goal = ""
        st.rerun()

    st.divider()

    # ── Chat History ──
    st.subheader("🕓 Chat History")
    all_sessions = get_all_sessions()

    if not all_sessions:
        st.caption("No chats yet. Click ➕ New Chat to begin!")
    else:
        for session_id, title, updated_at in all_sessions:
            date_str = updated_at[:10] if updated_at else ""
            is_active = (session_id == st.session_state.active_session_id)

            col1, col2 = st.columns([5, 1])
            with col1:
                label = f"**{title}**" if is_active else title
                if st.button(
                    label,
                    key=f"sess_{session_id}",
                    use_container_width=True,
                    help=f"Last updated: {date_str}"
                ):
                    # Load this session
                    st.session_state.active_session_id = session_id
                    transactions, goal = load_session_data(session_id)
                    st.session_state.transactions = transactions
                    st.session_state.user_goal = goal
                    st.rerun()

            with col2:
                if st.button("🗑", key=f"del_{session_id}", help="Delete"):
                    delete_session(session_id)
                    if st.session_state.active_session_id == session_id:
                        st.session_state.active_session_id = None
                        st.session_state.transactions = []
                        st.session_state.user_goal = ""
                    st.rerun()

    # ── Only show data section when a session is active ──
    if st.session_state.active_session_id:
        st.divider()
        st.subheader("📁 Transaction Data")

        if st.session_state.transactions:
            st.success(f"✅ {len(st.session_state.transactions)} transactions loaded")

        uploaded_file = st.file_uploader(
            "Upload Bank Statement CSV",
            type=["csv"],
            key=f"uploader_{st.session_state.active_session_id}"
        )

        if uploaded_file is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name
            parsed = parse_csv(tmp_path)
            st.session_state.transactions = parsed
            save_session_data(
                st.session_state.active_session_id,
                parsed,
                st.session_state.user_goal
            )
            st.success(f"✅ {len(parsed)} transactions loaded")

        st.divider()
        st.subheader("🎯 Your Goal")
        new_goal = st.text_input(
            "What are you saving for?",
            value=st.session_state.user_goal,
            placeholder="e.g. save 50000 for Goa in 4 months",
            key=f"goal_{st.session_state.active_session_id}"
        )
        if new_goal != st.session_state.user_goal:
            st.session_state.user_goal = new_goal
            save_session_data(
                st.session_state.active_session_id,
                st.session_state.transactions,
                new_goal
            )

        st.divider()
        st.subheader("⚡ Quick Actions")

        if st.button("📊 Analyze my spending", use_container_width=True):
            st.session_state.quick_message = "Analyze and categorize all my transactions"
            st.rerun()

        if st.button("💡 How can I save more?", use_container_width=True):
            st.session_state.quick_message = "Find me ways to save money on my biggest expenses"
            st.rerun()

        if st.button("📈 Am I on track?", use_container_width=True):
            st.session_state.quick_message = "Am I on track with my budget this month?"
            st.rerun()

# ─────────────────────────────────────────────
# MAIN CHAT AREA
# ─────────────────────────────────────────────

if not st.session_state.active_session_id:
    # Welcome screen
    st.markdown("""
        <div style='text-align:center; padding: 100px 20px'>
            <h1>💰 Personal Finance Advisor</h1>
            <p style='font-size:18px; color:gray'>
                Your AI-powered multi-agent financial assistant
            </p>
            <br>
            <p style='color:gray'>
                👈 Click <b>➕ New Chat</b> in the sidebar to get started
            </p>
        </div>
    """, unsafe_allow_html=True)

else:
    session_id = st.session_state.active_session_id

    # Load and display all past messages
    chat_history = get_messages(session_id)

    for msg in chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("agent"):
                st.caption(f"↳ {msg['agent']} agent")

    # ─────────────────────────────────────────────
    # HANDLE QUICK MESSAGE OR TYPED INPUT
    # ─────────────────────────────────────────────
    user_input = None

    # Check for quick action trigger
    if st.session_state.quick_message:
        user_input = st.session_state.quick_message
        st.session_state.quick_message = None

    # Chat input box — always visible when session is active
    typed_input = st.chat_input("Ask your finance advisor anything...",
                                 key=f"chat_{session_id}")
    if typed_input:
        user_input = typed_input

    # ─────────────────────────────────────────────
    # PROCESS INPUT
    # ─────────────────────────────────────────────
    if user_input:

        # Auto-title session from first message
        if not chat_history:
            update_session_title(session_id, user_input)

        # Show user message immediately
        with st.chat_message("user"):
            st.markdown(user_input)
        save_message(session_id, "user", user_input)
        
        

        # Run agents
        with st.chat_message("assistant"):
            status_box   = st.empty()   # shows which agent is running
            response_box = st.empty()   # streams the response
            agent_box    = st.empty()   # shows agent name at bottom

            full_response = ""
            agent_name    = "advisor"

            invoke_state = {
                "messages"      : [HumanMessage(content=user_input)],
                "transactions"  : st.session_state.transactions,
                "budget_summary": {},
                "savings_tips"  : [],
                "user_goal"     : st.session_state.user_goal,
                "next_agent"    : "",
                "final_response": ""
            }

            config = {"configurable": {"thread_id": session_id}}

            try:
                for chunk in st.session_state.app.stream(
                    invoke_state,
                    config=config,
                    stream_mode="updates"
                ):
                    for node_name, node_output in chunk.items():

                        # Show routing step
                        if node_name == "orchestrator":
                            next_a = node_output.get("next_agent", "")
                            if next_a:
                                status_box.caption(f"🔄 Routing to **{next_a}** agent...")
                            continue

                        # Stream agent response word by word
                        messages = node_output.get("messages", [])
                        if messages:
                            last_msg   = messages[-1]
                            full_response = last_msg.content
                            agent_name = getattr(last_msg, "name", node_name) or node_name

                            status_box.empty()
                            words    = full_response.split()
                            streamed = ""
                            for word in words:
                                streamed += word + " "
                                response_box.markdown(streamed + "▌")
                                time.sleep(0.02)

                            response_box.markdown(full_response)
                            agent_box.caption(f"↳ {agent_name} agent")

                # Persist to DB
                save_message(session_id, "assistant", full_response, agent_name)
                save_session_data(
                    session_id,
                    st.session_state.transactions,
                    st.session_state.user_goal
                )

            except Exception as e:
                response_box.error(f"Something went wrong: {str(e)}")
                st.caption("Please try again or start a new chat.")

        st.rerun()