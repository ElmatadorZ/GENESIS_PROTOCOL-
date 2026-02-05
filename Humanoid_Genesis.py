# humanoid_genesis.py
from __future__ import annotations
import os, json, time, uuid, math
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from pydantic import BaseModel
from dotenv import load_dotenv

# ===== Optional ROS2 hooks =====
ROS_AVAILABLE = False
try:
    import rclpy
    from rclpy.node import Node
    ROS_AVAILABLE = True
except Exception:
    ROS_AVAILABLE = False

# ===== Optional LLM providers =====
load_dotenv()
_USE_OPENAI = False
_USE_GEMINI = False
try:
    from openai import OpenAI
    _OPENAI = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    _USE_OPENAI = True if os.getenv("OPENAI_API_KEY") else False
except Exception:
    pass

try:
    import google.generativeai as genai
    if os.getenv("GEMINI_API_KEY"):
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        _USE_GEMINI = True
except Exception:
    pass

# -----------------------------
# LLM Interface (pluggable)
# -----------------------------
class LLM:
    def __init__(self,
                 model_openai: str="gpt-4o-mini",
                 model_gemini: str="gemini-1.5-pro"):
        self.model_openai = model_openai
        self.model_gemini = model_gemini
        if not (_USE_OPENAI or _USE_GEMINI):
            raise RuntimeError("No LLM provider configured.")

    def chat(self, system: str, user: str, temperature: float=0.5) -> str:
        if _USE_OPENAI:
            resp = _OPENAI.chat.completions.create(
                model=self.model_openai,
                temperature=temperature,
                messages=[{"role":"system","content":system},
                          {"role":"user","content":user}]
            )
            return resp.choices[0].message.content.strip()
        elif _USE_GEMINI:
            model = genai.GenerativeModel(
                model_name=self.model_gemini,
                system_instruction=system
            )
            resp = model.generate_content(user)
            return (resp.text or "").strip()
        else:
            return "LLM not available."

# -----------------------------
# Echo Memory (Genesis Echo Layer)
# -----------------------------
class EchoMemory:
    def __init__(self, path="humanoid_genesis_memory.json"):
        self.path = path
        self.state: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                self.state = json.load(open(self.path,"r",encoding="utf-8"))
            except Exception:
                self.state = {}

    def save(self):
        json.dump(self.state, open(self.path,"w",encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    def profile(self) -> Dict[str, Any]:
        return self.state.get("profile", {})

    def update_profile(self, signals: Dict[str, Any]):
        prof = self.profile()
        prof.update(signals)
        self.state["profile"] = prof
        self.save()

    def remember(self, key: str, value: Any):
        self.state[key] = value
        self.save()

    def recall(self, key: str, default=None):
        return self.state.get(key, default)

# -----------------------------
# Agents (Genesis Multi-Agent)
# -----------------------------
@dataclass
class Agent:
    name: str
    system: str
    style: str = "neutral"
    temperature: float = 0.6

    def run(self, llm: LLM, prompt: str, memory_hint: str="") -> str:
        sys = self.system + f"\n\nFollow style: {self.style}"
        usr = f"{memory_hint}\n\nTASK:\n{prompt}"
        return llm.chat(system=sys, user=usr, temperature=self.temperature)

# Agent presets (ปรับได้ตามโดเมน/งานของหุ่น)
ANALYST = Agent(
    name="Analyst",
    system=("You are an analytical planner for a humanoid robot. "
            "Extract facts, constraints, environment hints. List uncertainties."),
    style="analytical"
)
STRATEGIST = Agent(
    name="Strategist",
    system=("You convert analysis into practical plans. Provide options, tradeoffs, "
            "check resources, energy, time. Output step-by-step."),
    style="strategic"
)
SAFETY = Agent(
    name="Safety",
    system=("You are the safety officer. Evaluate planned actions against safety rules. "
            "Detect hazards (human proximity, impact force, heat, sharp tools, stairs, liquids). "
            "If risky, propose safer alternative."),
    style="concise"
)
DIALOG = Agent(
    name="Dialog",
    system=("You speak to humans politely. Summarize the robot's plan in simple language. "
            "Ask clarifying questions if needed. Keep tone friendly."),
    style="humane"
)
COSMIC = Agent(
    name="Cosmic",
    system=("You elevate reasoning to principles and long-horizon implications. "
            "Extract invariants, propose no-regret moves and learning updates."),
    style="philosophical", temperature=0.4
)

# -----------------------------
# Compound Mind
# -----------------------------
def compound_reasoning(llm: LLM, query: str, k_paths: int=4) -> List[str]:
    system = ("Generate multiple distinct reasoning paths for a humanoid task. "
              "Each path must differ in assumption/method/objective. "
              "Format as a numbered list.")
    text = llm.chat(system, f"Question:\n{query}\nGenerate {k_paths} perspectives.")
    # simple split
    lines = [l.strip("-• ").strip() for l in text.split("\n") if l.strip()]
    # keep top k
    return lines[:k_paths] if lines else [text]

# -----------------------------
# Resonance Alignment (merge)
# -----------------------------
def resonance_alignment(llm: LLM, drafts: Dict[str,str],
                        user_intent: str, memory_hint: str) -> str:
    merged = "\n\n".join([f"[{k}]\n{v}" for k,v in drafts.items()])
    system = ("You are the Resonance Aligner. Merge all agent drafts into "
              "a single coherent plan suitable for a humanoid robot. "
              "Preserve safety and clarity. Output sections: "
              "(Objective) (Plan) (Checks) (Fallback).")
    usr = f"USER INTENT:\n{user_intent}\n\nMEMORY:\n{memory_hint}\n\nDRAFTS:\n{merged}"
    return llm.chat(system, usr, temperature=0.5)

# -----------------------------
# Safety Rules (hard constraints)
# -----------------------------
SAFETY_RULES = [
    "Never move if emergency_stop is TRUE.",
    "Keep minimum 0.7m from human unless explicit permission.",
    "Max hand speed 0.5 m/s near humans.",
    "Do not pick hot/sharp objects without proper tool.",
    "Avoid liquid spills on electronics.",
    "Announce intent before moving."
]

def safety_gate(draft_plan: str) -> Dict[str, Any]:
    """ตรวจเบื้องต้นแบบกฎแข็ง (rule-based quick scan)"""
    violations = []
    if "run" in draft_plan.lower():  # ตัวอย่างง่าย ๆ
        violations.append("Potential high speed near humans.")
    return {"ok": len(violations)==0, "violations": violations}

# -----------------------------
# Humanoid Runtime (Perceive → Think → Act)
# -----------------------------
class HumanoidGenesisRuntime:
    def __init__(self, llm: LLM, memory: EchoMemory,
                 ros_enabled: bool=ROS_AVAILABLE):
        self.llm = llm
        self.memory = memory
        self.ros_enabled = ros_enabled
        self.emergency_stop = False
        if self.ros_enabled:
            rclpy.init()
            self.node = Node("genesis_humanoid")
            # TODO: subscribe sensors/estop topics here

    # --- Perceive (mock/simple) ---
    def perceive(self) -> Dict[str, Any]:
        # ในจริง: อ่านกล้อง, lidar, pose, battery, temp, human proximity …
        world = {
            "battery": 0.83,
            "temp_c": 41.2,
            "humans_nearby": True,
            "dist_min_human_m": 1.2,
        }
        return world

    # --- Think (Genesis + Compound + Cosmic) ---
    def think(self, user_goal: str, world: Dict[str,Any]) -> Dict[str, str]:
        mem_hint = json.dumps({
            "profile": self.memory.profile(),
            "world": world,
            "safety_rules": SAFETY_RULES
        }, ensure_ascii=False)

        perspectives = compound_reasoning(self.llm, user_goal, k_paths=4)

        prompt = (f"Goal:\n{user_goal}\n\nPerspectives:\n" +
                  "\n".join(f"- {p}" for p in perspectives))

        drafts = {
            "Analyst": ANALYST.run(self.llm, prompt, mem_hint),
            "Strategist": STRATEGIST.run(self.llm, prompt, mem_hint),
            "Safety": SAFETY.run(self.llm, prompt, mem_hint),
            "Dialog": DIALOG.run(self.llm, prompt, mem_hint)
        }

        merged = resonance_alignment(self.llm, drafts, user_goal, mem_hint)

        # Safety hard gate (ภายใน)
        g = safety_gate(merged)
        if not g["ok"]:
            merged += f"\n\n[SAFETY NOTICE] Violations: {g['violations']}"

        cosmic = COSMIC.run(self.llm,
                            f"Base plan:\n{merged}\n\nPropose learning updates.",
                            mem_hint)

        return {"plan": merged, "cosmic": cosmic}

    # --- Act (mock actuator) ---
    def act(self, plan_text: str):
        # ในงานจริง: แปลง plan เป็น motion primitives / nav goals / speech
        print("\n[HUMANOID ACTION]\n")
        # พูดกับมนุษย์ (หรือ ROS topic / TTS)
        say = f"I will follow this plan safely.\n{plan_text[:400]}..."
        print(say)

    # --- Loop ---
    def run_once(self, user_goal: str):
        if self.emergency_stop:
            print("[E-STOP] Abort.")
            return
        world = self.perceive()
        out = self.think(user_goal, world)
        print("\n— GENESIS PLAN —\n", out["plan"])
        print("\n— COSMIC MIND —\n", out["cosmic"])
        self.act(out["plan"])

# -----------------------------
# Example
# -----------------------------
if __name__ == "__main__":
    llm = LLM()  # auto-picks provider from env
    mem = EchoMemory()
    # ตั้งโปรไฟล์หุ่นให้เป็น “Money Atlas style”
    if not mem.profile():
        mem.update_profile({
            "identity": "Money Atlas Humanoid",
            "tone": "cinematic+calm",
            "domain": ["human_interaction","safe_manipulation","coffee_service"]
        })

    runtime = HumanoidGenesisRuntime(llm, mem, ros_enabled=False)
    goal = "Serve a hot pour-over coffee to a guest at table A, then clean the station."
    runtime.run_once(goal)
            
