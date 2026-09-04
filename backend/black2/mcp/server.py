"""Model Context Protocol (MCP) Server for Pokémon Black 2 Semantic Runtime.

Standard JSON-RPC 2.0 MCP Interface (STDIO Transport).
Provides intuitive high-level semantic tools for AI agents.
"""

import sys
import json
import asyncio
from typing import Dict, Any, List
from .skills import PokemonAgentSkills

skills = PokemonAgentSkills()

MCP_TOOLS = [
    {
        "name": "pokemon_observe",
        "description": "【核心感知工具】直观获取当前游戏界面、所处地图、当前对话内容、可选选项与推荐按键。AI 无需自己读内存，直接读取本工具返回的语义信息做出决策。",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "pokemon_press_button",
        "description": "【基础操作工具】按下虚拟 NDS 按键。用于选选项、确认对话、移动方向或开始游戏。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "button": {
                    "type": "string",
                    "enum": ["A", "B", "X", "Y", "Up", "Down", "Left", "Right", "Start", "Select"],
                    "description": "需要按下的按键名称。"
                },
                "frames": {
                    "type": "integer",
                    "description": "按住的帧数（默认 4 帧）。",
                    "default": 4
                }
            },
            "required": ["button"]
        }
    },
    {
        "name": "pokemon_type_text",
        "description": "【起名与文字输入】在起名/键盘输入界面，输入指定的名字文本（例如 'zero'）并自动完成键盘映射与确认。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要输入的姓名或文本（例如 'zero'）。"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "pokemon_advance_dialogue",
        "description": "【对话推进工具】推进当前正在显示的剧情对话（按 A 键翻页）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "integer",
                    "description": "需要连续翻过几句对话（默认 1）。",
                    "default": 1
                }
            },
            "required": []
        }
    },
    {
        "name": "pokemon_select_option",
        "description": "【菜单与选项选择】在选择题（如男/女、是/否）或主菜单中移动光标并按 A 确认。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["Up", "Down", "Left", "Right"],
                    "description": "移动光标的方向。"
                },
                "confirm": {
                    "type": "boolean",
                    "description": "移动后是否立即按 A 键确认（默认 true）。",
                    "default": True
                }
            },
            "required": []
        }
    }
]


def handle_tool_call(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "pokemon_observe":
        return skills.observe()
    elif tool_name == "pokemon_press_button":
        return skills.press_button(args.get("button", "A"), args.get("frames", 4))
    elif tool_name == "pokemon_type_text":
        return skills.type_text(args.get("text", "zero"))
    elif tool_name == "pokemon_advance_dialogue":
        return skills.advance_dialogue(args.get("steps", 1))
    elif tool_name == "pokemon_select_option":
        return skills.select_menu_option(args.get("direction", "Down"), args.get("confirm", True))
    else:
        return {"error": f"Unknown tool: {tool_name}"}


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    while True:
        try:
            line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "pokemon-black2-semantic-runtime-mcp",
                            "version": "1.0.0"
                        }
                    }
                }
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": MCP_TOOLS
                    }
                }
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                result_data = handle_tool_call(tool_name, tool_args)
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result_data, ensure_ascii=False, indent=2)
                            }
                        ]
                    }
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found"
                    }
                }

            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32603,
                    "message": str(e)
                }
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
