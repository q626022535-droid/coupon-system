"""
券码领取系统 - MongoDB版本
"""
import streamlit as st
import hashlib
import os
from datetime import datetime
import io
from pymongo import MongoClient
from bson import ObjectId

# MongoDB 连接配置
MONGO_URI = "mongodb://admin:lyn050227@cluster0-shard-00-00.drkdhrz.mongodb.net:27017,cluster0-shard-00-01.drkdhrz.mongodb.net:27017,cluster0-shard-00-02.drkdhrz.mongodb.net:27017/coupon_system?ssl=true&replicaSet=Cluster0-shard-0&authSource=admin"
DB_NAME = "coupon_system"

_client = None

def get_db():
    """获取 MongoDB 数据库连接"""
    global _client
    if _client is None:
        if not MONGO_URI:
            st.error("❌ 错误：未设置 MONGO_URI 环境变量")
            raise ValueError("MONGO_URI not configured")
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=30000)
    return _client[DB_NAME]

def init_db():
    """初始化数据库"""
    try:
        db = get_db()
        db.users.create_index("username", unique=True)
        db.coupons.create_index("code", unique=True)
        db.sessions.create_index("token", unique=True)
        return True
    except Exception as e:
        st.error(f"数据库初始化失败: {e}")
        return False

def hash_password(password):
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    """验证密码"""
    return hash_password(password) == hashed

def generate_token():
    """生成随机token"""
    return hashlib.sha256(os.urandom(32)).hexdigest()

def get_current_user():
    """获取当前登录用户"""
    if "token" not in st.session_state:
        return None
    db = get_db()
    session = db.sessions.find_one({"token": st.session_state.token})
    if session:
        return db.users.find_one({"_id": session["user_id"]})
    return None

def login_user(username, password):
    """用户登录"""
    db = get_db()
    user = db.users.find_one({"username": username})
    if user and verify_password(password, user["password"]):
        token = generate_token()
        db.sessions.insert_one({
            "token": token,
            "user_id": user["_id"],
            "created_at": datetime.now()
        })
        st.session_state.token = token
        return True
    return False

def logout_user():
    """用户登出"""
    if "token" in st.session_state:
        db = get_db()
        db.sessions.delete_one({"token": st.session_state.token})
        del st.session_state.token

def add_coupon(code, amount, created_by):
    """添加券码"""
    db = get_db()
    try:
        db.coupons.insert_one({
            "code": code,
            "amount": amount,
            "created_by": created_by,
            "created_at": datetime.now(),
            "claimed": False
        })
        return True
    except Exception:
        return False

def claim_coupon(code, claimed_by):
    """领取券码"""
    db = get_db()
    coupon = db.coupons.find_one({"code": code})
    if coupon and not coupon.get("claimed"):
        db.coupons.update_one(
            {"code": code},
            {"$set": {
                "claimed": True,
                "claimed_by": claimed_by,
                "claimed_at": datetime.now()
            }}
        )
        return coupon["amount"]
    return None

def get_coupons(claimed=None):
    """获取券码列表"""
    db = get_db()
    query = {}
    if claimed is not None:
        query["claimed"] = claimed
    return list(db.coupons.find(query).sort("created_at", -1))

def export_coupons_to_csv():
    """导出券码为CSV"""
    import csv
    coupons = get_coupons()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["券码", "金额", "状态", "创建时间", "领取人", "领取时间"])
    for c in coupons:
        writer.writerow([
            c["code"],
            c["amount"],
            "已领取" if c.get("claimed") else "未领取",
            c["created_at"].strftime("%Y-%m-%d %H:%M") if "created_at" in c else "",
            c.get("claimed_by", ""),
            c["claimed_at"].strftime("%Y-%m-%d %H:%M") if c.get("claimed_at") else ""
        ])
    return output.getvalue()

def main():
    """主函数"""
    st.set_page_config(page_title="券码领取系统", page_icon="🎫")
    
    # 初始化数据库
    if not init_db():
        st.stop()
    
    # 检查登录状态
    user = get_current_user()
    
    if not user:
        # 登录页面
        st.title("🎫 券码领取系统")
        st.subheader("用户登录")
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        if st.button("登录"):
            if login_user(username, password):
                st.success("登录成功！")
                st.rerun()
            else:
                st.error("用户名或密码错误")
    else:
        # 主界面
        st.title(f"🎫 券码领取系统 - 欢迎 {user['username']}")
        
        col1, col2 = st.columns([3, 1])
        with col2:
            if st.button("退出登录"):
                logout_user()
                st.rerun()
        
        # 管理员功能
        if user.get("is_admin"):
            st.subheader("📤 批量导入券码")
            uploaded_file = st.file_uploader("上传CSV文件 (格式: 券码,金额)", type=["csv"])
            if uploaded_file:
                import csv
                content = uploaded_file.read().decode("utf-8")
                reader = csv.reader(content.splitlines())
                count = 0
                for row in reader:
                    if len(row) >= 2:
                        code, amount = row[0], row[1]
                        if add_coupon(code, amount, user["username"]):
                            count += 1
                st.success(f"成功导入 {count} 个券码")
            
            if st.button("📥 导出所有券码"):
                csv_data = export_coupons_to_csv()
                st.download_button(
                    "下载CSV",
                    csv_data,
                    file_name=f"coupons_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
        
        # 领取券码
        st.subheader("🎁 领取券码")
        coupon_code = st.text_input("输入券码")
        if st.button("领取"):
            result = claim_coupon(coupon_code, user["username"])
            if result:
                st.success(f"成功领取！金额: {result}")
            else:
                st.error("券码无效或已被领取")
        
        # 券码列表
        st.subheader("📋 券码列表")
        coupons = get_coupons()
        if coupons:
            for c in coupons:
                status = "✅ 已领取" if c.get("claimed") else "⏳ 未领取"
                st.write(f"{c['code']} - {c['amount']}元 - {status}")
        else:
            st.info("暂无券码")

if __name__ == "__main__":
    main()
