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
    applying_coupons = list(get_db().coupons.find({"issued_to": user_name, "status": "applying"}).sort("_id", -1))

    if applying_coupons:
        st.subheader("⏳ 待审批的申请")
        data = []
        for c in applying_coupons:
            data.append({"金额": f"¥{c['amount']}", "申请时间": c.get("apply_time") or "-", "状态": "审核中"})
        import pandas as pd
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.divider()

    # 领取新券码
    available_amounts = get_available_amounts()

    st.subheader("📥 领取新券码")

    if available_amounts:
        col1, col2 = st.columns(2)

        with col1:
            selected_amount = st.selectbox(
                "选择券码金额",
                available_amounts,
                format_func=lambda x: f"¥{x}",
                key="amount_select"
            )

        with col2:
            max_count = get_available_coupons_by_amount(selected_amount)
            if max_count > 0:
                quantity = st.number_input("领取数量", min_value=1, max_value=max_count, value=1, key="qty_input")
                st.write(f"可选数量: {max_count} 张")
            else:
                quantity = 0
                st.warning("该金额暂无券码")

        if quantity > 0:
            if st.button(f"申请领取 {quantity} 张 ¥{selected_amount} 券码", type="primary", use_container_width=True):
                coupons = list(get_db().coupons.find({"status": "pending", "amount": selected_amount}).limit(quantity))

                if len(coupons) == quantity:
                    for c in coupons:
                        get_db().coupons.update_one(
                            {"_id": c["_id"]},
                            {"$set": {"status": "applying", "issued_to": user_name, "department": "用户领取",
                                      "apply_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}
                        )
                    st.success(f"已提交申请，等待管理员审批！")
                    st.rerun()
                else:
                    st.error("券码数量不足")
    else:
        st.info("暂无可领取的券码，请联系管理员上传")

def page_apply():
    st.set_page_config(page_title="申请领取", page_icon="🎫")
    
    st.title("申请领取券码")
    
    if "apply_coupon_id" in st.session_state:
        coupon_id = st.session_state["apply_coupon_id"]
        
        with st.form("apply_form"):
            name = st.text_input("姓名", value=st.session_state.get("name", ""))
            department = st.text_input("事业部")
            submit = st.form_submit_button("提交申请")
            
            if submit:
                if name and department:
                    apply_coupon(coupon_id, name, department)
                    st.success("申请已提交，等待审批！")
                    del st.session_state["apply_coupon_id"]
                    st.rerun()
                else:
                    st.error("请填写完整信息")
        
        if st.button("取消"):
            del st.session_state["apply_coupon_id"]
            st.rerun()
    else:
        st.warning("请先选择要领取的券码")
        if st.button("返回"):
            st.rerun()

def page_user_records():
    st.set_page_config(page_title="我的券码", page_icon="🎫")

    st.title("🎫 我的券码")

    records = list(get_db().coupons.find({"issued_to": st.session_state.get("name", "")}).sort("_id", -1))

    if records:
        data = []
        for c in records:
            status_emoji = {"pending": "⏳", "applying": "📝", "issued": "✅", "used": "🎉"}.get(c["status"], "❓")
            data.append({"券码": c["code"], "金额": f"¥{c['amount']}", "状态": f"{status_emoji} {c['status']}", "领取时间": c.get("issue_time") or "-"})

        import pandas as pd
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True)

        # 可导出
        if st.button("导出我的券码"):
            csv = df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button("下载CSV", csv, "my_coupons.csv", "text/csv")
    else:
        st.info("暂无领取记录")

def page_approver_home():
    st.set_page_config(page_title="审批管理", page_icon="📋")

    st.title("📋 审批管理")

    # 待审批列表
    applying = list(get_db().coupons.find({"status": "applying"}).sort("apply_time", 1))

    st.subheader(f"待审批 ({len(applying)})")
    if applying:
        for coupon in applying:
            with st.container():
                st.write(f"**券码**: {coupon['code']} | **金额**: ¥{coupon['amount']}")
                st.write(f"**申请人**: {coupon.get('issued_to')} | **事业部**: {coupon.get('department')} | **申请时间**: {coupon.get('apply_time')}")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ 批准", key=f"app_{coupon['_id']}"):
                        approve_coupon(str(coupon["_id"]), "approve")
                        st.rerun()
                with col2:
                    if st.button("❌ 拒绝", key=f"rej_{coupon['_id']}"):
                        approve_coupon(str(coupon["_id"]), "reject")
                        st.rerun()
                st.divider()
    else:
        st.info("暂无待审批的申请")

def page_approver_records():
    st.set_page_config(page_title="审批记录", page_icon="📊")
    
    st.title("📊 审批记录")
    
    stats = get_statistics()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("待领取", stats.get("pending", 0))
    col2.metric("申请中", stats.get("applying", 0))
    col3.metric("已发放", stats.get("issued", 0))
    col4.metric("已使用", stats.get("used", 0))
    
    st.subheader("所有券码")
    all_coupons = get_coupons()
    
    data = []
    for c in all_coupons:
        data.append({"ID": c[0], "券码": c[1], "金额": c[2], "状态": c[3], "领取人": c[4] or "-", "事业部": c[5] or "-", "申请时间": c[6] or "-", "发放时间": c[7] or "-"})
    
    if data:
        import pandas as pd
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True)

def page_admin_home():
    st.set_page_config(page_title="管理后台", page_icon="⚙️")

    st.title("⚙️ 券码管理")

    # 统计
    stats = get_statistics()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("待领取", stats.get("pending", 0))
    col2.metric("申请中", stats.get("applying", 0))
    col3.metric("已领取", stats.get("issued", 0))
    col4.metric("总计", sum(stats.values()))

    # 待审批列表
    st.subheader("📋 待审批")
    applying = list(get_db().coupons.find({"status": "applying"}).sort("_id", -1))

    if applying:
        # 按申请人分组统计
        applicants = {}
        for c in applying:
            name = c.get("issued_to")
            if name not in applicants:
                applicants[name] = {"count": 0, "amount": 0, "ids": []}
            applicants[name]["count"] += 1
            applicants[name]["amount"] += c["amount"]
            applicants[name]["ids"].append(str(c["_id"]))

        # 显示待审批列表
        data = []
        for c in applying:
            data.append({"ID": str(c["_id"]), "券码": c["code"], "金额": c["amount"], "申请人": c.get("issued_to"), "申请时间": c.get("apply_time") or "-"})
        import pandas as pd
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True)

        # 按用户批次审批
        st.subheader("⚡ 审批操作（按用户批次）")

        st.write("**待审批用户：**")
        for name, info in applicants.items():
            st.write(f"• {name}: {info['count']}张券，总计 ¥{info['amount']}")

        col1, col2 = st.columns(2)
        with col1:
            approve_user = st.selectbox("选择要批准的用户", list(applicants.keys()), key="approve_user")
            if st.button(f"✅ 批准 {approve_user} 的全部申请", type="primary"):
                ids = applicants[approve_user]["ids"]
                for aid in ids:
                    get_db().coupons.update_one({"_id": ObjectId(aid)},
                        {"$set": {"status": "issued", "issue_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}})
                st.success(f"已批准 {approve_user} 的 {len(ids)} 个申请")
                st.rerun()
        with col2:
            reject_user = st.selectbox("选择要拒绝的用户", list(applicants.keys()), key="reject_user")
            if st.button(f"❌ 拒绝 {reject_user} 的全部申请"):
                ids = applicants[reject_user]["ids"]
                for rid in ids:
                    get_db().coupons.update_one({"_id": ObjectId(rid)},
                        {"$set": {"status": "pending", "issued_to": None, "department": None, "apply_time": None}})
                st.success(f"已拒绝 {reject_user} 的 {len(ids)} 个申请")
                st.rerun()
    else:
        st.info("暂无待审批的申请")

    # 上传券码
    st.subheader("📤 上传券码")
    st.download_button("下载模板", "券码\n券码2\n券码3", "coupon_template.txt", "text/plain")

    with st.form("upload_form"):
        uploaded_file = st.file_uploader("上传券码文件（txt格式，每行一个）", type=["txt"])
        amount = st.number_input("金额（元）", min_value=0.0, value=100.0)
        submit = st.form_submit_button("上传")
        if submit and uploaded_file:
            content = uploaded_file.getvalue().decode("utf-8")
            codes = content.strip().split("\n")
            added = upload_coupons(codes, amount)
            st.success(f"成功上传 {added} 个券码")

    # 券码列表
    st.subheader("📋 券码列表")
    all_coupons = get_coupons()
    data = []
    for c in all_coupons:
        data.append({"ID": c[0], "券码": c[1], "金额": c[2], "状态": c[3], "领取人": c[4] or "-", "事业部": c[5] or "-", "领取时间": c[7] or "-"})

    if data:
        import pandas as pd
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True)

        # 删除功能
        st.subheader("🗑️ 删除券码")
        st.write("**方式一：手动选择**")
        coupon_ids = [c[0] for c in all_coupons]
        selected = st.multiselect("选择要删除的券码ID", coupon_ids, key="delete_ids")
        if st.button("删除选中", key="delete_selected"):
            if selected:
                deleted = delete_coupons_by_ids(selected)
                st.success(f"成功删除 {deleted} 个券码")
                st.rerun()

        st.divider()
        st.write("**方式二：上传文件批量删除**")
        st.info("上传txt文件，每行一个券码或券码ID")

        with st.form("batch_delete_form"):
            delete_file = st.file_uploader("上传券码文件", type=["txt"], key="delete_file")
            delete_submit = st.form_submit_button("批量删除")
            if delete_submit and delete_file:
                content = delete_file.getvalue().decode("utf-8")
                lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
                if lines:
                    # 判断是ID还是券码（MongoDB ID是24位十六进制）
                    if len(lines[0]) == 24 and all(c in '0123456789abcdef' for c in lines[0].lower()):
                        deleted = delete_coupons_by_ids(lines)
                        st.success(f"按ID删除成功：{deleted} 个券码")
                    else:
                        deleted = delete_coupons_by_codes(lines)
                        st.success(f"按券码删除成功：{deleted} 个券码")
                    st.rerun()

def page_admin_users():
    st.set_page_config(page_title="用户管理", page_icon="👥", layout="wide")
    
    # 科技感样式
    st.markdown("""
    <style>
    .tech-title {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-align: center;
        margin-bottom: 2rem;
        text-shadow: 0 0 30px rgba(102, 126, 234, 0.3);
    }
    .user-card {
        background: linear-gradient(145deg, #1e1e2e 0%, #2d2d44 100%);
        border: 1px solid rgba(102, 126, 234, 0.3);
        border-radius: 12px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
        transition: all 0.3s ease;
    }
    .user-card:hover {
        border-color: rgba(102, 126, 234, 0.6);
        box-shadow: 0 6px 25px rgba(102, 126, 234, 0.2);
        transform: translateY(-2px);
    }
    .role-badge {
        display: inline-block;
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
    }
    .role-super_admin { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; }
    .role-approver { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); color: white; }
    .role-user { background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); color: white; }
    .stat-card {
        background: linear-gradient(145deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid rgba(102, 126, 234, 0.2);
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.2);
    }
    .stat-number {
        font-size: 2rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .action-btn {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border: none;
        color: white;
        padding: 0.5rem 1rem;
        border-radius: 8px;
        cursor: pointer;
        transition: all 0.3s ease;
    }
    .action-btn:hover {
        transform: scale(1.05);
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<h1 class="tech-title">👥 用户管理系统</h1>', unsafe_allow_html=True)
    
    # 统计卡片
    users = get_all_users()
    total_users = len(users)
    admin_count = sum(1 for u in users if u[2] == "super_admin")
    approver_count = sum(1 for u in users if u[2] == "approver")
    normal_count = sum(1 for u in users if u[2] == "user")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="stat-card"><div class="stat-number">{total_users}</div><div>总用户数</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="stat-card"><div class="stat-number">{admin_count}</div><div>超级管理员</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="stat-card"><div class="stat-number">{approver_count}</div><div>审批员</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="stat-card"><div class="stat-number">{normal_count}</div><div>普通用户</div></div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 添加用户区域
    with st.expander("➕ 添加新用户", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            new_username = st.text_input("用户名", key="new_username")
        with col2:
            new_password = st.text_input("密码", type="password", key="new_password")
        with col3:
            new_role = st.selectbox("角色", ["user", "approver", "super_admin"], key="new_role")
        
        if st.button("添加用户", type="primary", use_container_width=True):
            if not new_username or not new_password:
                st.error("用户名和密码不能为空")
            elif add_user(new_username, new_password, new_role, new_username, ""):
                st.success("✅ 用户添加成功")
                st.rerun()
            else:
                st.error("用户名已存在")
    
    st.markdown("---")
    
    # 用户列表
    st.markdown("### 📋 用户列表")
    
    if users:
        for u in users:
            user_id, username, role, name, dept = u
            
            # 角色样式映射
            role_class = f"role-{role}"
            role_names = {"super_admin": "🔧 超级管理员", "approver": "📋 审批员", "user": "👤 普通用户"}
            
            # 用户卡片
            st.markdown(f"""
            <div class="user-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h3 style="margin: 0; color: #e0e0e0;">{username}</h3>
                        <span class="role-badge {role_class}">{role_names.get(role, role)}</span>
                    </div>
                    <div style="color: #888; font-size: 0.9rem;">ID: {user_id}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # 操作按钮（不显示在超级管理员卡片上）
            if role != "super_admin":
                col1, col2, col3 = st.columns([2, 2, 6])
                
                with col1:
                    # 修改角色
                    new_role_select = st.selectbox(
                        "修改角色", 
                        ["user", "approver", "super_admin"],
                        index=["user", "approver", "super_admin"].index(role),
                        key=f"role_{user_id}"
                    )
                    if new_role_select != role:
                        if st.button("确认修改", key=f"update_role_{user_id}"):
                            success, msg = update_user_role(user_id, new_role_select)
                            if success:
                                st.success(f"✅ {msg}")
                                st.rerun()
                            else:
                                st.error(msg)
                
                with col2:
                    # 删除按钮
                    if st.button("🗑️ 删除用户", key=f"delete_{user_id}"):
                        success, msg = delete_user(user_id)
                        if success:
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(msg)
                
                st.markdown("<div style='margin-bottom: 1rem;'></div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='margin-bottom: 1rem; color: #888; font-size: 0.85rem;'>💡 超级管理员不可修改或删除</div>", unsafe_allow_html=True)
    else:
        st.info("暂无用户数据")

def main():
    # 初始化
    init_db()
    
    # 登录检查 - 始终从 URL token 验证，确保会话隔离
    token = st.query_params.get("token")
    user = verify_login_by_token(token) if token else None
    
    if not user:
        # 未认证 - 显示登录页
        page_login()
        return
    
    # 已认证 - 设置会话状态（每次请求都重新验证）
    st.session_state["logged_in"] = True
    st.session_state["user_id"] = user[0]
    st.session_state["username"] = user[4]
    st.session_state["role"] = user[1]
    st.session_state["name"] = user[2] or user[4]
    st.session_state["department"] = user[3] or ""
    
    # 侧边栏
    role = st.session_state.get("role", "")
    
    st.sidebar.title(f"用户: {st.session_state.get('name', '')}")
    st.sidebar.write(f"角色: {role}")
    
    if role == "super_admin":
        menu = ["首页", "券码管理", "用户管理"]
    elif role == "approver":
        menu = ["首页", "审批管理", "审批记录"]
    elif role == "user":
        menu = ["首页", "我的券码"]
    else:
        menu = ["首页"]
    
    choice = st.sidebar.radio("菜单", menu)
    
    if st.sidebar.button("退出登录"):
        clear_session_token(st.session_state.get("user_id"), token=st.query_params.get("token"))  # 只清除当前会话
        st.session_state.clear()
        st.query_params.clear()  # 清除URL参数
        st.rerun()
    
    # 页面路由
    if choice == "首页":
        if role == "super_admin":
            page_admin_home()
        elif role == "approver":
            page_approver_home()
        else:
            page_user_home()
    elif choice == "券码管理":
        page_admin_home()
    elif choice == "用户管理":
        page_admin_users()
    elif choice == "我的券码":
        page_user_records()

if __name__ == "__main__":
    main()