"""Template library used when the Claude API is not configured (and as the fallback
when it is). Every text item is a translation key: the English text lives in
locales/en/content.json and each language has its own content.json.
"""

PROJECT_TYPES = ["software", "cybersecurity", "ai_ml", "cloud", "mobile", "data", "generic"]

# keywords (any language) used to guess the project type from the user's prompt
TYPE_KEYWORDS = {
    "cybersecurity": ["cyber", "security", "sécurité", "securite", "seguridad", "sicherheit", "sicurezza", "segurança",
                      "soc", "siem", "pentest", "threat", "menace", "fraud", "fraude", "सुरक्षा", "सायबर", "साइबर",
                      "أمن", "セキュリティ", "安全", "firewall", "vulnerab", "malware", "ransomware", "iso 27001"],
    "ai_ml": ["ai", " ia ", "machine learning", "deep learning", "ml ", "model", "modèle", "neural", "llm", "chatbot",
              "prediction", "prédiction", "intelligence artificielle", "künstliche", "inteligencia", "intelligenza",
              "एआय", "एआई", "कृत्रिम", "ذكاء", "人工知能", "人工智能", "机器学习", "nlp", "computer vision"],
    "cloud": ["cloud", "aws", "azure", "gcp", "kubernetes", "k8s", "migration", "devops", "terraform", "infra",
              "क्लाउड", "سحاب", "クラウド", "云", "nube", "nuvem", "datacenter", "serverless"],
    "mobile": ["mobile", "android", "ios", "app store", "play store", "smartphone", "मोबाईल", "मोबाइल", "جوال",
               "モバイル", "移动", "móvil", "aplicativo", "handy"],
    "data": ["data", "données", "donnees", "datos", "dados", "dati", "daten", "etl", "warehouse", "dashboard",
             "analytics", "bi ", "power bi", "tableau", "डेटा", "बिग डेटा", "بيانات", "データ", "数据", "reporting"],
    "software": ["website", "site web", "web", "platform", "plateforme", "plataforma", "piattaforma", "plattform",
                 "software", "logiciel", "application", "app", "saas", "erp", "crm", "portal", "portail",
                 "सॉफ्टवेअर", "सॉफ्टवेयर", "वेबसाइट", "प्लॅटफॉर्म", "प्लेटफ़ॉर्म", "برنامج", "منصة", "ソフトウェア",
                 "ウェブ", "软件", "网站", "平台"],
}

PHASES = ["initiation", "planning", "design", "build", "testing", "deployment", "closure"]

# code, phase, base_days, role, deps, deliverable, priority
COMMON_TASKS = [
    ("kickoff", "initiation", 1, "pm", [], None, "high"),
    ("doc_register", "initiation", 1, "pm", ["kickoff"], "documents_needed", "high"),
    ("proposal", "initiation", 2, "pm", ["kickoff"], "proposal", "medium"),
    ("charter", "initiation", 2, "pm", ["proposal"], "charter", "high"),
    ("stakeholders", "initiation", 1, "ba", ["kickoff"], None, "medium"),
    ("ms_charter", "initiation", 0, "pm", ["charter", "stakeholders", "doc_register"], None, "high"),

    ("requirements", "planning", 4, "ba", ["ms_charter"], "brd", "high"),
    ("srs", "planning", 4, "lead", ["requirements"], "srs", "high"),
    ("wbs_schedule", "planning", 2, "pm", ["requirements"], "wbs", "high"),
    ("risk_plan", "planning", 2, "pm", ["requirements"], "risk_assessment", "medium"),
    ("budget_plan", "planning", 1, "pm", ["wbs_schedule"], None, "medium"),
    ("ms_plan", "planning", 0, "pm", ["srs", "wbs_schedule", "risk_plan", "budget_plan"], None, "high"),

    ("architecture", "design", 4, "lead", ["ms_plan"], "architecture", "high"),
    ("tech_design", "design", 3, "lead", ["architecture"], "tdd", "high"),
    ("impl_plan", "design", 1, "pm", ["architecture"], "implementation_plan", "medium"),
    ("test_strategy", "design", 2, "qa", ["ms_plan"], "test_plan", "medium"),
    ("ms_design", "design", 0, "pm", ["tech_design", "impl_plan", "test_strategy", "@design"], None, "high"),

    ("env_setup", "build", 2, "dev", ["ms_design"], None, "high"),
    ("integration", "build", 3, "lead", ["@build"], None, "high"),
    ("user_manual", "build", 2, "ba", ["integration"], "user_manual", "low"),
    ("ms_build", "build", 0, "pm", ["integration", "user_manual"], None, "high"),

    ("functional_tests", "testing", 4, "qa", ["ms_build"], None, "high"),
    ("uat", "testing", 3, "ba", ["functional_tests", "@testing"], None, "high"),
    ("fix_defects", "testing", 3, "dev", ["uat"], None, "high"),
    ("ms_testing", "testing", 0, "pm", ["fix_defects"], None, "high"),

    ("deploy_guide", "deployment", 2, "lead", ["ms_build"], "deployment_guide", "medium"),
    ("go_live", "deployment", 1, "lead", ["ms_testing", "deploy_guide"], None, "critical"),
    ("ms_golive", "deployment", 0, "pm", ["go_live"], None, "critical"),
    ("training", "deployment", 2, "pm", ["ms_golive"], None, "medium"),

    ("closure_report", "closure", 2, "pm", ["training"], "closure_report", "medium"),
    ("lessons", "closure", 1, "pm", ["training"], None, "low"),
    ("ms_closed", "closure", 0, "pm", ["closure_report", "lessons"], None, "high"),
]

# per type: one design task, three build tasks (chained after env_setup), one test task
TYPE_TASKS = {
    "software": [("ux_design", "design", 3, "design"), ("backend", "build", 6, "dev"), ("frontend", "build", 6, "dev"),
                 ("database", "build", 3, "dev"), ("performance", "testing", 2, "qa")],
    "cybersecurity": [("threat_model", "design", 3, "sec"), ("siem", "build", 5, "sec"), ("detection", "build", 5, "sec"),
                      ("incident", "build", 3, "sec"), ("pentest", "testing", 3, "sec")],
    "ai_ml": [("data_strategy", "design", 3, "data"), ("data_pipeline", "build", 5, "data"), ("model_training", "build", 6, "ml"),
              ("model_serving", "build", 3, "ml"), ("model_eval", "testing", 3, "ml")],
    "cloud": [("landing_zone", "design", 3, "cloud"), ("iac", "build", 5, "cloud"), ("migration", "build", 6, "cloud"),
              ("observability", "build", 3, "cloud"), ("dr_test", "testing", 2, "cloud")],
    "mobile": [("mobile_ux", "design", 3, "design"), ("mobile_app", "build", 8, "dev"), ("mobile_api", "build", 5, "dev"),
               ("store_prep", "build", 2, "design"), ("device_tests", "testing", 3, "qa")],
    "data": [("data_model", "design", 3, "data"), ("etl", "build", 6, "data"), ("warehouse", "build", 4, "data"),
             ("dashboards", "build", 4, "dev"), ("data_quality", "testing", 3, "qa")],
    "generic": [("solution_design", "design", 3, "lead"), ("build_core", "build", 6, "dev"), ("build_extra", "build", 4, "dev"),
                ("internal_review", "build", 2, "qa"), ("pilot", "testing", 3, "qa")],
}

ROLES = {"pm": "project_manager", "ba": "business_analyst", "lead": "tech_lead", "dev": "developer",
         "qa": "qa_engineer", "design": "designer", "sec": "security_engineer", "ml": "ml_engineer",
         "data": "data_engineer", "cloud": "cloud_engineer"}

TEAM_ORDER = {
    "software": ["pm", "lead", "dev", "qa", "design", "ba", "dev", "dev"],
    "cybersecurity": ["pm", "sec", "lead", "sec", "qa", "ba", "dev", "sec"],
    "ai_ml": ["pm", "ml", "data", "lead", "dev", "qa", "ba", "ml"],
    "cloud": ["pm", "cloud", "lead", "cloud", "qa", "dev", "ba", "cloud"],
    "mobile": ["pm", "dev", "design", "lead", "qa", "dev", "ba", "dev"],
    "data": ["pm", "data", "lead", "data", "qa", "ba", "dev", "data"],
    "generic": ["pm", "lead", "dev", "qa", "ba", "design", "dev", "dev"],
}

# when nobody has the role, fall back along this chain
ROLE_FALLBACK = {
    "sec": ["lead", "dev", "pm"], "ml": ["data", "dev", "lead", "pm"], "data": ["dev", "lead", "pm"],
    "cloud": ["lead", "dev", "pm"], "design": ["dev", "ba", "pm"], "ba": ["pm"], "qa": ["dev", "lead", "pm"],
    "lead": ["dev", "pm"], "dev": ["lead", "pm"], "pm": ["lead", "dev"],
}

# daily rates (EUR) used to cost personnel by role
DAILY_RATE_EUR = {"pm": 520, "ba": 430, "lead": 560, "dev": 450, "qa": 400, "design": 420,
                  "sec": 540, "ml": 560, "data": 480, "cloud": 520}

BUDGET_CATEGORIES = ["personnel", "software_licences", "cloud_hosting", "hardware", "training", "security_testing", "contingency"]
BUDGET_SPLIT = {
    "software": [62, 6, 8, 4, 3, 5, 12],
    "cybersecurity": [55, 14, 5, 5, 4, 7, 10],
    "ai_ml": [55, 5, 18, 5, 3, 4, 10],
    "cloud": [50, 6, 25, 2, 3, 4, 10],
    "mobile": [65, 6, 6, 5, 2, 4, 12],
    "data": [58, 10, 14, 2, 3, 3, 10],
    "generic": [60, 8, 6, 6, 5, 3, 12],
}

# code, category, probability, impact, owner role
COMMON_RISKS = [
    ("scope_creep", "scope", 3, 4, "pm"),
    ("schedule_delay", "schedule", 3, 4, "pm"),
    ("budget_overrun", "cost", 3, 3, "pm"),
    ("key_person", "resource", 2, 4, "pm"),
    ("unclear_requirements", "scope", 3, 3, "ba"),
    ("integration_issues", "technical", 3, 3, "lead"),
    ("data_protection", "compliance", 2, 5, "lead"),
    ("vendor_delay", "external", 2, 3, "pm"),
]
TYPE_RISKS = {
    "software": [("tech_debt", "technical", 3, 3, "lead"), ("performance", "technical", 2, 4, "lead")],
    "cybersecurity": [("false_positives", "technical", 4, 3, "sec"), ("log_gaps", "security", 3, 4, "sec")],
    "ai_ml": [("data_quality", "technical", 4, 4, "data"), ("model_bias", "compliance", 3, 4, "ml")],
    "cloud": [("cost_spike", "cost", 3, 3, "cloud"), ("migration_downtime", "technical", 2, 5, "cloud")],
    "mobile": [("store_rejection", "external", 2, 3, "design"), ("device_fragmentation", "technical", 3, 3, "qa")],
    "data": [("source_changes", "technical", 3, 3, "data"), ("data_quality", "technical", 4, 4, "data")],
    "generic": [("stakeholder_availability", "resource", 3, 3, "pm"), ("quality_issues", "technical", 2, 3, "qa")],
}

# per type: product features (used for requirements, user manual, tests) and architecture components
FEATURES = {t: [f"{t}.f{i}" for i in range(1, 6)] for t in PROJECT_TYPES}
COMPONENTS = {t: [f"{t}.c{i}" for i in range(1, 5)] for t in PROJECT_TYPES}
NFRS = ["security", "performance", "availability", "usability", "scalability", "maintainability", "localization"]

DOC_TYPES = ["documents_needed", "charter", "proposal", "brd", "srs", "tdd", "architecture", "wbs",
             "implementation_plan", "test_plan", "risk_assessment", "deployment_guide", "user_manual", "closure_report"]
REPORT_TYPES = ["daily", "weekly", "monthly", "missed", "budget", "risk", "workload"]
