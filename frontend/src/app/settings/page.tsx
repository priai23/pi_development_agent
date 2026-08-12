"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { API_URL } from "@/lib/api";
import { Save, Loader2, KeyRound, Cpu, Search, ChevronDown, Check } from "lucide-react";

export default function SettingsPage() {
  const [openRouterKey, setOpenRouterKey] = useState("");
  const [modelName, setModelName] = useState("anthropic/claude-3.5-sonnet");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  
  const [models, setModels] = useState<{id: string, name: string}[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Fetch Settings
    fetch(`${API_URL}/settings/`)
      .then((res) => res.json())
      .then((data: any[]) => {
        const keySetting = data.find((s) => s.key === "openrouter_api_key");
        const modelSetting = data.find((s) => s.key === "llm_model_name");
        
        if (keySetting) setOpenRouterKey(keySetting.value);
        if (modelSetting) setModelName(modelSetting.value);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Error fetching settings:", err);
        setLoading(false);
      });

    // Fetch OpenRouter Models
    fetch("https://openrouter.ai/api/v1/models")
      .then((res) => res.json())
      .then((data) => {
        if (data && data.data) {
          setModels(data.data.map((m: any) => ({ id: m.id, name: m.name })));
        }
      })
      .catch((err) => console.error("Error fetching OpenRouter models:", err));
      
    // Click outside to close dropdown
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setMessage("");

    const settingsToUpdate = [
      { key: "openrouter_api_key", value: openRouterKey },
      { key: "llm_model_name", value: modelName }
    ];

    try {
      const res = await fetch(`${API_URL}/settings/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsToUpdate),
      });

      if (res.ok) {
        setMessage("Settings saved successfully.");
      } else {
        setMessage("Failed to save settings.");
      }
    } catch (err) {
      console.error(err);
      setMessage("Error saving settings.");
    } finally {
      setSaving(false);
    }
  };

  const filteredModels = models.filter(m => 
    m.id.toLowerCase().includes(searchQuery.toLowerCase()) || 
    m.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const selectedModelObj = models.find(m => m.id === modelName);

  if (loading) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto bg-gray-50 dark:bg-black p-8">
      <div className="max-w-2xl mx-auto">
        <div className="mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-white mb-2">
            Settings
          </h1>
          <p className="text-gray-500 dark:text-gray-400">
            Configure global agent properties and API keys.
          </p>
        </div>

        <motion.div 
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 30 }}
          className="glass-panel p-6 rounded-2xl"
        >
          <form onSubmit={handleSave} className="space-y-6">
            
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-300">
                <KeyRound className="w-4 h-4" />
                OpenRouter API Key
              </label>
              <input
                type="password"
                value={openRouterKey}
                onChange={(e) => setOpenRouterKey(e.target.value)}
                placeholder="sk-or-v1-..."
                className="w-full px-4 py-3 bg-white/50 dark:bg-black/50 border border-gray-200 dark:border-gray-800 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 transition-shadow"
              />
              <p className="text-xs text-gray-500">Your key is stored securely in the local database.</p>
            </div>

            <div className="space-y-2 relative" ref={dropdownRef}>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-300">
                <Cpu className="w-4 h-4" />
                LLM Model
              </label>
              
              <div 
                className="w-full px-4 py-3 bg-white/50 dark:bg-black/50 border border-gray-200 dark:border-gray-800 rounded-xl cursor-pointer flex justify-between items-center transition-shadow hover:bg-white/80 dark:hover:bg-gray-900/80"
                onClick={() => setDropdownOpen(!dropdownOpen)}
              >
                <div className="truncate pr-4 flex flex-col">
                  {selectedModelObj ? (
                    <>
                      <span className="font-medium text-gray-900 dark:text-white truncate">{selectedModelObj.name}</span>
                      <span className="text-xs text-gray-500 truncate">{selectedModelObj.id}</span>
                    </>
                  ) : (
                    <span className="font-medium text-gray-900 dark:text-white truncate">{modelName}</span>
                  )}
                </div>
                <ChevronDown className={`w-5 h-5 text-gray-400 transition-transform ${dropdownOpen ? 'rotate-180' : ''}`} />
              </div>

              <AnimatePresence>
                {dropdownOpen && (
                  <motion.div 
                    initial={{ opacity: 0, height: 0, overflow: 'hidden' }}
                    animate={{ opacity: 1, height: 'auto', overflow: 'visible' }}
                    exit={{ opacity: 0, height: 0, overflow: 'hidden' }}
                    transition={{ duration: 0.2, ease: "easeInOut" }}
                    className="w-full mt-2"
                  >
                    <div className="bg-white dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-xl shadow-xl overflow-hidden">
                      <div className="p-2 border-b border-gray-100 dark:border-gray-800 relative">
                        <Search className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" />
                        <input 
                          type="text" 
                          placeholder="Search models..." 
                          value={searchQuery}
                          onChange={(e) => setSearchQuery(e.target.value)}
                          className="w-full pl-9 pr-4 py-2 bg-gray-50 dark:bg-gray-900 border-none rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/50 text-sm"
                          autoFocus
                        />
                      </div>
                      
                      <div className="max-h-64 overflow-y-auto p-1 scrollbar-thin scrollbar-thumb-gray-200 dark:scrollbar-thumb-gray-800">
                        {filteredModels.length === 0 ? (
                          <div className="p-4 text-center text-sm text-gray-500">No models found</div>
                        ) : (
                          filteredModels.map((m) => (
                            <div 
                              key={m.id}
                              onClick={() => {
                                setModelName(m.id);
                                setDropdownOpen(false);
                              }}
                              className={`flex items-center justify-between p-3 cursor-pointer rounded-lg transition-colors ${modelName === m.id ? 'bg-blue-50 dark:bg-blue-500/10' : 'hover:bg-gray-50 dark:hover:bg-gray-900/50'}`}
                            >
                              <div className="flex flex-col truncate pr-4">
                                <span className={`text-sm truncate ${modelName === m.id ? 'font-medium text-blue-600 dark:text-blue-400' : 'text-gray-900 dark:text-gray-200'}`}>{m.name}</span>
                                <span className="text-xs text-gray-500 truncate">{m.id}</span>
                              </div>
                              {modelName === m.id && <Check className="w-4 h-4 text-blue-500 flex-shrink-0" />}
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {message && (
              <div className={`p-3 rounded-lg text-sm ${message.includes("success") ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400" : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"}`}>
                {message}
              </div>
            )}

            <div className="pt-4 flex justify-end">
              <motion.button
                whileTap={{ scale: 0.97 }}
                type="submit"
                disabled={saving}
                className="flex items-center gap-2 px-6 py-3 bg-blue-500 hover:bg-blue-600 text-white font-medium rounded-xl transition-colors disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Save Settings
              </motion.button>
            </div>
          </form>
        </motion.div>
      </div>
    </div>
  );
}
