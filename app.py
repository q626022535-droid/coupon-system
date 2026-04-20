"""
券码领取系统 - MongoDB版本 (适配 CloudBase 部署)
"""
import streamlit as st
import hashlib
import os
from datetime import datetime
import io
import secrets
from pymongo import MongoClient
from bson import ObjectId

# MongoDB 连接配置 - 支持环境变量和 Streamlit secrets
# CloudBase 环境变量优先
MONGO_URI = os.environ.get("MONGO_URI") or st.secrets.get("MONGO_URI", "")
DB_NAME = os.environ.get("DB_NAME") or st.secrets.get("DB_NAME", "coupon_system")

_client = None

def get_db():
    """获取 MongoDB 数据库连接 - 支持 SRV 和标准连接字符串"""
    global _client
    if _client is None:
        if not MONGO_URI:
            st.error("❌ 错误：未设置 MONGO_URI 环境变量")
            st.info("请在 CloudBase 控制台 → 服务管理 → 环境变量中添加 MONGO_URI")
            raise ValueError("MONGO_URI not configured")
        
        # 判断连接字符串类型
        is_srv = MONGO_URI.startswith("mongodb+srv://")
        
        # 构建连接选项
        client_options = {
            "connect": False,  # 延迟连接
            "serverSelectionTimeoutMS": 30000,
            "connectTimeoutMS": 30000,
            "socketTimeoutMS": 30000,
            "retryWrites": True,
        }
        
        # SRV 连接在某些容器环境（如 CloudBase）可能有 DNS 问题
        # 如果是 SRV 且连接失败，提示用户使用标准连接字符串
        try:
            _client = MongoClient(MONGO_URI, **client_options)
        except Exception as e:
            if is_srv and "DNS" in str(e):
                st.error("❌ MongoDB SRV 连接失败（DNS 解析问题）")
                st.info("💡 解决方案：去 MongoDB Atlas 获取标准连接字符串（mongodb:// 开头）")
                st.code("""mongodb://admin:密码@cluster0-shard-00-00.drkdhrz.mongodb.net:27017,...""", language="text")
            raise
    return _client[DB_NAME]

def init_db():
    """初始化数据库 - 创建索引和默认管理员"""
    try:
        db = get_db()
        db.users.create_index("username", unique=True)
        db.coupons.create_index("code", unique=True)
        db.sessions.create_index("token", unique=True)
        db.sessions.create_index("expires_at")  # 用于快速清理过期session
        if not db.users.find_one({"username": "admin"}):
            db.users.insert_one({
                "username": "admin",
                "password": hashlib.sha256("admin123".encode()).hexdigest(),
                "role": "super_admin",
                "name": "管理员",
                "department": "",
                "session_token": None
            })
    except Exception as e:
        st.error(f"❌ 数据库初始化失败: {e}")
        raise

# 认证函数
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_login(username, password):
    user = get_db().users.find_one({"username": username, "password": hash_password(password)})
    if user:
        return (str(user["_id"]), user["role"], user["name"], user["department"])
    return None

def verify_login_by_token(token):
    """验证 token - 从 sessions 集合查找，永不过期"""
    if not token:
        return None
    # 查找 session（永不过期）
    session = get_db().sessions.find_one({"token": token})
    if session:
        user = get_db().users.find_one({"_id": session["user_id"]})
        if user and user.get("role") != "disabled":
            # 更新最后活跃时间
            get_db().sessions.update_one({"_id": session["_id"]}, {"$set": {"last_active": datetime.now()}})
            return (str(user["_id"]), user["role"], user["name"], user["department"], user["username"])
    return None

def generate_session_token(user_id):
    """生成会话 token - 每个登录独立 token，永不过期"""
    token = secrets.token_hex(32)
    now = datetime.now()
    get_db().sessions.insert_one({
        "token": token,
        "user_id": ObjectId(user_id),
        "created_at": now,
        "last_active": now,
        "expires_at": None  # 永不过期
    })
    return token

def clear_session_token(user_id, token=None):
    """清除会话 token - 支持清除单个会话或全部会话"""
    if token:
        get_db().sessions.delete_one({"token": token})
    else:
        get_db().sessions.delete_many({"user_id": ObjectId(user_id)})

def add_user(username, password, role, name, department=""):
    try:
        get_db().users.insert_one({
            "username": username, "password": hash_password(password),
            "role": role, "name": name, "department": department, "session_token": None
        })
        return True
    except:
        return False

def get_all_users():
    users = list(get_db().users.find().sort("_id", 1))
    return [(str(u["_id"]), u["username"], u["role"], u["name"], u["department"]) for u in users]

def delete_user(user_id):
    user = get_db().users.find_one({"_id": ObjectId(user_id)})
    if user and user["role"] == "super_admin":
        return False, "不能删除超级管理员"
    get_db().users.delete_one({"_id": ObjectId(user_id)})
    return True, "删除成功"

def update_user_role(user_id, new_role):
    user = get_db().users.find_one({"_id": ObjectId(user_id)})
    if user and user["role"] == "super_admin":
        return False, "不能修改超级管理员角色"
    get_db().users.update_one({"_id": ObjectId(user_id)}, {"$set": {"role": new_role}})
    return True, "角色修改成功"

def update_user_password(user_id, new_password):
    get_db().users.update_one({"_id": ObjectId(user_id)}, {"$set": {"password": hash_password(new_password)}})
    return True

# 券码管理函数
def upload_coupons(codes, amount):
    db = get_db()
    added = 0
    for code in codes:
        code = code.strip()
        if code:
            try:
                db.coupons.insert_one({
                    "code": code, "amount": float(amount), "status": "pending",
                    "issued_to": None, "department": None, "apply_time": None, "issue_time": None
                })
                added += 1
            except:
                pass
    return added

def get_coupons(status=None):
    query = {"status": status} if status else {}
    coupons = list(get_db().coupons.find(query).sort("_id", -1))
    return [(str(c["_id"]), c["code"], c["amount"], c["status"], c.get("issued_to"), c.get("department"), c.get("apply_time"), c.get("issue_time")) for c in coupons]

def get_available_coupons():
    coupons = list(get_db().coupons.find({"status": "pending"}).sort("amount", -1))
    return [(str(c["_id"]), c["code"], c["amount"]) for c in coupons]

def apply_coupon(coupon_id, name, department):
    get_db().coupons.update_one(
        {"_id": ObjectId(coupon_id), "status": "pending"},
        {"$set": {"status": "applying", "issued_to": name, "department": department,
                  "apply_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}
    )

def approve_coupon(coupon_id, action):
    db = get_db()
    if action == "approve":
        db.coupons.update_one({"_id": ObjectId(coupon_id)},
            {"$set": {"status": "issued", "issue_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}})
    else:
        db.coupons.update_one({"_id": ObjectId(coupon_id)},
            {"$set": {"status": "pending", "issued_to": None, "department": None, "apply_time": None, "issue_time": None}})

def delete_coupon(coupon_id):
    get_db().coupons.delete_one({"_id": ObjectId(coupon_id)})

def delete_coupons_by_ids(coupon_ids):
    result = get_db().coupons.delete_many({"_id": {"$in": [ObjectId(cid) for cid in coupon_ids]}})
    return result.deleted_count

def delete_coupons_by_codes(codes):
    codes = [c.strip() for c in codes if c.strip()]
    result = get_db().coupons.delete_many({"code": {"$in": codes}})
    return result.deleted_count

def get_statistics():
    pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    return {s["_id"]: s["count"] for s in get_db().coupons.aggregate(pipeline)}

# ====== 页面函数 ======

def page_login():
    st.set_page_config(page_title="券码领取系统", page_icon="🎫")
    
    st.markdown("""
    <style>
    .login-container {
        max-width: 400px;
        margin: 50px auto;
        padding: 30px;
        background: white;
        border-radius: 10px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
    }
    .stTitle { text-align: center; color: #1f77b4; }
    </style>
    """, unsafe_allow_html=True)
    
    # 检查是否需要显示注册表单
    if st.session_state.get("show_register"):
        page_register()
        return
    
    st.title("🎫 券码领取系统")
    
    with st.form("login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submit = st.form_submit_button("登录", use_container_width=True)
        
        if submit:
            user = verify_login(username, password)
            if user:
                # 检查用户是否被禁用
                if user[1] == "disabled":
                    st.error("账号已被禁用，请联系管理员")
                else:
                    st.session_state["logged_in"] = True
                    st.session_state["user_id"] = user[0]
                    st.session_state["username"] = username
                    st.session_state["role"] = user[1]
                    st.session_state["name"] = user[2] or username
                    st.session_state["department"] = user[3] or ""
                    # 生成token并设置URL参数
                    token = generate_session_token(user[0])
                    st.query_params["token"] = token
                    st.rerun()
            else:
                st.error("用户名或密码错误")
    
    st.markdown("---")
    
    # 注册按钮
    if st.button("➕ 注册新账号", use_container_width=True):
        st.session_state["show_register"] = True
        st.rerun()

def page_register():
    """注册页面 - 只需姓名和密码"""
    st.set_page_config(page_title="用户注册", page_icon="➕")
    
    st.title("➕ 用户注册")
    
    with st.form("register_form"):
        name = st.text_input("姓名（作为登录账号）")
        password = st.text_input("密码", type="password")
        confirm_password = st.text_input("确认密码", type="password")
        
        submit = st.form_submit_button("注册", use_container_width=True)
        
        if submit:
            if not name or not password:
                st.error("请填写所有必填项")
            elif password != confirm_password:
                st.error("两次输入的密码不一致")
            elif len(password) < 6:
                st.error("密码长度至少6位")
            else:
                # 直接注册成功，无需审批
                if add_user(name, password, "user", name, ""):
                    st.success("注册成功！现在可以登录")
                    st.session_state["show_register"] = False
                    if st.button("立即登录"):
                        st.rerun()
                else:
                    st.error("姓名已存在，请更换")
    
    if st.button("← 返回登录"):
        st.session_state["show_register"] = False
        st.rerun()

def get_available_amounts():
    """获取所有可用的券码金额"""
    return sorted(get_db().coupons.distinct("amount", {"status": "pending"}), reverse=True)

def get_available_coupons_by_amount(amount):
    """根据金额获取可用的券码数量"""
    return get_db().coupons.count_documents({"status": "pending", "amount": amount})

def page_user_home():
    st.set_page_config(page_title="券码领取", page_icon="🎫")

    st.title(f"🎫 欢迎，{st.session_state.get('name', '')}")

    # 显示已审批通过的券码
    user_name = st.session_state.get("name", "")
    my_coupons = list(get_db().coupons.find({"issued_to": user_name, "status": "issued"}).sort("_id", -1))

    if my_coupons:
        st.subheader("🎉 已领取的券码")
        data = []
        for c in my_coupons:
            data.append({"券码": c["code"], "金额": f"¥{c['amount']}", "领取时间": c.get("issue_time") or "-"})
        import pandas as pd
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.divider()

    # 显示待审批的申请
    applying_coupons = list(get_db().coupons.find({"issued_to": user_name, "status":
