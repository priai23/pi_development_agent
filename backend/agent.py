import xmlrpc.client
import json
import asyncio
import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain.tools import tool

class OdooClient:
    def __init__(self, url, db, username, password):
        self.url = url
        self.db = db
        self.username = username
        self.password = password
        self.common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        self.uid = self.common.authenticate(db, username, password, {})
        if not self.uid:
            raise Exception("Authentication failed")
        self.models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

    def version(self):
        return self.common.version()

    def search_read(self, model, domain=[], fields=[], limit=None):
        kwargs = {'fields': fields}
        if limit:
            kwargs['limit'] = limit
        return self.models.execute_kw(self.db, self.uid, self.password,
            model, 'search_read',
            [domain],
            kwargs)
            
    def execute(self, model, method, *args, **kwargs):
        """Generic method to execute any model method on Odoo"""
        return self.models.execute_kw(self.db, self.uid, self.password,
            model, method,
            args,
            kwargs)

class PiERPClient:
    def __init__(self, url, username, password):
        self.url = url
        self.username = username
        self.password = password
        self.is_connected = True
        
class ERPImplementationAgent:
    def __init__(self, erp_type: str, client, workspace_path: str = None, llm_model: str = "gpt-4o-mini", api_key: str = None, base_url: str = None):
        self.erp_type = erp_type
        self.client = client
        self.workspace_path = workspace_path
        
        # Ensure workspace exists
        if self.workspace_path:
            os.makedirs(self.workspace_path, exist_ok=True)
        
        kwargs = {
            "model": llm_model,
            "temperature": 0,
            "streaming": True
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
            
        self.llm = ChatOpenAI(**kwargs)

        @tool
        def inspect_instance() -> str:
            """Gets basic information about the connected ERP instance (version, modules, etc.)."""
            if self.erp_type == "odoo":
                try:
                    return json.dumps(self.client.version())
                except Exception as e:
                    return f"Error: {str(e)}"
            return "Pi ERP version information mock"

        @tool
        def installed_modules() -> str:
            """Lists the core modules/apps currently installed in the ERP system."""
            if self.erp_type == "odoo":
                try:
                    modules = self.client.search_read('ir.module.module', [('state', '=', 'installed')], ['name', 'shortdesc'])
                    return json.dumps(modules)
                except Exception as e:
                    return f"Error: {str(e)}"
            return "Pi ERP installed modules mock"

        @tool
        def inspect_company() -> str:
            """Retrieves the details of the main company configured in the ERP."""
            if self.erp_type == "odoo":
                try:
                    company = self.client.search_read('res.company', [], ['name', 'email', 'phone', 'currency_id'], limit=1)
                    return json.dumps(company)
                except Exception as e:
                    return f"Error: {str(e)}"
            return "Pi ERP company info mock"

        @tool
        def inspect_users() -> str:
            """Retrieves a summary of users in the ERP system."""
            if self.erp_type == "odoo":
                try:
                    users = self.client.search_read('res.users', [], ['name', 'login'], limit=10)
                    return json.dumps(users)
                except Exception as e:
                    return f"Error: {str(e)}"
            return "Pi ERP users info mock"

        @tool
        def inspect_sales_config() -> str:
            """Retrieves the current sales configuration settings."""
            if self.erp_type == "odoo":
                try:
                    settings = self.client.search_read('res.config.settings', [], ['group_sale_order_template', 'module_delivery'], limit=1)
                    return json.dumps(settings)
                except Exception as e:
                    return f"Error: {str(e)}"
            return "Pi ERP sales config mock"

        @tool
        def inspect_purchase_config() -> str:
            """Retrieves the current purchase configuration settings."""
            if self.erp_type == "odoo":
                try:
                    settings = self.client.search_read('res.config.settings', [], ['group_warning_purchase', 'module_purchase_requisition'], limit=1)
                    return json.dumps(settings)
                except Exception as e:
                    return f"Error: {str(e)}"
            return "Pi ERP purchase config mock"

        @tool
        def inspect_inventory_config() -> str:
            """Retrieves the current inventory and warehouse configuration settings."""
            if self.erp_type == "odoo":
                try:
                    settings = self.client.search_read('res.config.settings', [], ['group_stock_multi_locations', 'group_stock_adv_location'], limit=1)
                    return json.dumps(settings)
                except Exception as e:
                    return f"Error: {str(e)}"
            return "Pi ERP inventory config mock"

        @tool
        def install_module(module_name: str) -> str:
            """Installs a module/app in the ERP system by its technical name (e.g., 'crm', 'sale_management')."""
            if self.erp_type == "odoo":
                try:
                    # Find the module ID
                    modules = self.client.search_read('ir.module.module', [('name', '=', module_name)], ['id', 'state'], limit=1)
                    if not modules:
                        return f"Error: Module '{module_name}' not found."
                    if modules[0]['state'] == 'installed':
                        return f"Module '{module_name}' is already installed."
                    
                    # Install it
                    module_id = modules[0]['id']
                    self.client.execute('ir.module.module', 'button_immediate_install', [module_id])
                    return f"Successfully triggered installation for '{module_name}'."
                except Exception as e:
                    return f"Error installing module: {str(e)}"
            return f"Pi ERP: Mock installing {module_name}"

        @tool
        def update_company(company_id: int, updates: dict) -> str:
            """Updates company information. Provide the company_id (usually 1) and a dictionary of fields to update (e.g., {'name': 'New Name', 'email': 'info@new.com'})."""
            if self.erp_type == "odoo":
                try:
                    self.client.execute('res.company', 'write', [company_id], updates)
                    return f"Successfully updated company {company_id} with {updates}."
                except Exception as e:
                    return f"Error updating company: {str(e)}"
            return f"Pi ERP: Mock updating company {company_id}"

        @tool
        def create_user(name: str, login: str, email: str) -> str:
            """Creates a new user in the ERP system. Requires name, login (username), and email."""
            if self.erp_type == "odoo":
                try:
                    user_data = {
                        'name': name,
                        'login': login,
                        'email': email,
                        'sel_groups_1_8_9': 8 # Basic Internal User access
                    }
                    new_user_id = self.client.execute('res.users', 'create', user_data)
                    return f"Successfully created user '{name}' with ID {new_user_id}."
                except Exception as e:
                    return f"Error creating user: {str(e)}"
            return f"Pi ERP: Mock creating user {name}"
        @tool
        def list_directory(path: str = "") -> str:
            """Lists all files and directories in the specified path within the workspace."""
            try:
                if not self.workspace_path:
                    return "No workspace configured for this project."
                full_path = os.path.abspath(os.path.join(self.workspace_path, path))
                if not full_path.startswith(os.path.abspath(self.workspace_path)):
                    return "Error: Path is outside the workspace."
                if not os.path.exists(full_path):
                    return f"Path {path} does not exist."
                items = os.listdir(full_path)
                return json.dumps(items)
            except Exception as e:
                return f"Error listing directory: {str(e)}"

        @tool
        def create_directory(path: str) -> str:
            """Creates a new directory in the workspace."""
            try:
                if not self.workspace_path:
                    return "No workspace configured."
                full_path = os.path.abspath(os.path.join(self.workspace_path, path))
                if not full_path.startswith(os.path.abspath(self.workspace_path)):
                    return "Error: Path is outside the workspace."
                os.makedirs(full_path, exist_ok=True)
                return f"Successfully created directory {path}"
            except Exception as e:
                return f"Error creating directory: {str(e)}"

        @tool
        def write_file(filepath: str, content: str) -> str:
            """Writes content to a file in the workspace. Creates it if it doesn't exist."""
            try:
                if not self.workspace_path:
                    return "No workspace configured."
                full_path = os.path.abspath(os.path.join(self.workspace_path, filepath))
                if not full_path.startswith(os.path.abspath(self.workspace_path)):
                    return "Error: Path is outside the workspace."
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                return f"Successfully wrote to {filepath}"
            except Exception as e:
                return f"Error writing file: {str(e)}"

        @tool
        def read_file(filepath: str) -> str:
            """Reads the content of a file in the workspace."""
            try:
                if not self.workspace_path:
                    return "No workspace configured."
                full_path = os.path.abspath(os.path.join(self.workspace_path, filepath))
                if not full_path.startswith(os.path.abspath(self.workspace_path)):
                    return "Error: Path is outside the workspace."
                if not os.path.exists(full_path):
                    return f"File {filepath} does not exist."
                with open(full_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception as e:
                return f"Error reading file: {str(e)}"

        self.tools = [
            inspect_instance, installed_modules, inspect_company, 
            inspect_users, inspect_sales_config, inspect_purchase_config, 
            inspect_inventory_config, install_module, update_company, create_user,
            list_directory, create_directory, write_file, read_file
        ]
        
        # Load the Odoo 19 Knowledge Base
        kb_path = os.path.join(os.path.dirname(__file__), "..", "skills", "odoo19-dev", "Odoo19_Dev_Customization_KB.md")
        kb_content = ""
        if os.path.exists(kb_path):
            with open(kb_path, 'r', encoding='utf-8') as f:
                kb_content = f.read()

        system_prompt = f"""You are a specialized Odoo 19 Custom Module Developer Agent.
You not only inspect ERP configurations, but your primary objective is to BUILD custom Odoo modules by writing Python and XML code into your local workspace.

You have filesystem tools (list_directory, create_directory, write_file, read_file) to scaffold modules in the workspace.
Always ensure you create the necessary `__init__.py`, `__manifest__.py`, `models/`, and `views/` directories and files for Odoo.

CRITICAL ODOO 19 KNOWLEDGE BASE:
The following are the core guidelines for building Odoo 19 modules. Use this knowledge strictly:
{kb_content}

CRITICAL BEHAVIORAL RULES:
1. Always create a proper directory structure for your modules.
2. If asked to build a module, start by outlining your plan, then create the directories, then write the files.
3. The Grounding Law: Every factual claim about a customer, record, or configuration state MUST trace back to a tool call result.
"""
        system_message = SystemMessage(content=system_prompt)
        
        self.memory = MemorySaver()
        self.agent_executor = create_react_agent(
            self.llm, 
            self.tools, 
            prompt=system_message,
            checkpointer=self.memory,
            interrupt_before=["tools"]
        )

    def _convert_history(self, interactions: list):
        history = []
        for i in interactions:
            if i.role == "user":
                history.append(HumanMessage(content=i.content))
            elif i.role == "agent":
                history.append(AIMessage(content=i.content))
        return history

    async def astream(self, prompt: str, thread_id: str, interactions: list = None, resume_action: str = None):
        config = {"configurable": {"thread_id": thread_id}}
        
        if resume_action:
            state = self.agent_executor.get_state(config)
            if not state.next:
                yield "The agent session was restarted, so the pending action was lost. Please re-send your last prompt."
                return

            if resume_action == "reject":
                last_message = state.values.get("messages", [])[-1] if state.values.get("messages") else None
                if last_message:
                    tool_calls = getattr(last_message, "tool_calls", [])
                    from langchain_core.messages import ToolMessage
                    messages_to_add = []
                    for tc in tool_calls:
                        messages_to_add.append(ToolMessage(
                            tool_call_id=tc["id"], 
                            content="User rejected this action.", 
                            name=tc["name"]
                        ))
                    self.agent_executor.update_state(config, {"messages": messages_to_add}, as_node="tools")
            input_data = None
        else:
            if interactions is None:
                interactions = []
            
            chat_history = self._convert_history(interactions)
            chat_history.append(HumanMessage(content=prompt))
            input_data = {"messages": chat_history}
            
        safe_tools = [
            "inspect_instance", "inspect_users", "inspect_sales_config", 
            "inspect_purchase_config", "inspect_inventory_config",
            "list_directory", "read_file"
        ]

        while True:
            async for event in self.agent_executor.astream_events(
                input_data,
                config,
                version="v1"
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content and isinstance(content, str):
                        yield content
                        
            state = self.agent_executor.get_state(config)
            if not state.next:
                break
                
            if 'tools' in state.next:
                last_message = state.values["messages"][-1]
                tool_calls = getattr(last_message, "tool_calls", [])
                
                requires_approval = False
                pending_tool = None
                for tc in tool_calls:
                    if tc["name"] not in safe_tools:
                        requires_approval = True
                        pending_tool = tc
                        break
                
                if requires_approval:
                    import json
                    payload = json.dumps({"tool": pending_tool["name"], "args": pending_tool["args"]})
                    yield f"\n_ACTION_PENDING_||{payload}\n"
                    break
                else:
                    input_data = None
            else:
                input_data = None
