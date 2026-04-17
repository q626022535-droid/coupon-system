import os
import sys
import json
import zipfile
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tcb.v20180608 import tcb_client, models

# 配置
ENV_ID = "coupon-system-1gpvz5bh17d61cbf"
REGION = "ap-shanghai"
SECRET_ID = "AKIDXw88wZzB9tOgmfN1TlsoX5PYUDjYSL2X"
SECRET_KEY = "KbgZGkz3va2GOK2VknTwDJBpFRRDl5D7"

def create_zip():
    """创建部署包"""
    zip_path = "deploy.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 添加主应用
        zf.write("app.py")
        # 添加数据库
        if os.path.exists("coupon_system.db"):
            zf.write("coupon_system.db")
        # 添加配置
        if os.path.exists(".streamlit/config.toml"):
            zf.write(".streamlit/config.toml", ".streamlit/config.toml")
    return zip_path

def deploy_to_tcb():
    """部署到腾讯云云开发"""
    cred = credential.Credential(SECRET_ID, SECRET_KEY)
    httpProfile = HttpProfile()
    httpProfile.endpoint = "tcb.tencentcloudapi.com"
    clientProfile = ClientProfile()
    clientProfile.httpProfile = httpProfile
    client = tcb_client.TcbClient(cred, REGION, clientProfile)
    
    # 创建云函数
    req = models.CreateCloudBaseRunServerVersionRequest()
    req.EnvId = ENV_ID
    req.UploadType = "ZIP"
    req.VersionName = "v1"
    req.DockerfilePath = "Dockerfile"
    req.Cpu = "0.25"
    req.Mem = "0.5"
    req.MinNum = "0"
    req.MaxNum = "1"
    req.PolicyType = "cpu"
    req.PolicyThreshold = "60"
    req.ContainerPort = "8501"
    req.ServerName = "coupon-system"
    req.EnvParams = json.dumps({"PORT": "8501"})
    
    # 读取zip文件
    zip_path = create_zip()
    with open(zip_path, 'rb') as f:
        import base64
        req.Code = base64.b64encode(f.read()).decode('utf-8')
    
    resp = client.CreateCloudBaseRunServerVersion(req)
    print(f"部署成功: {resp.to_json_string()}")
    return resp

if __name__ == "__main__":
    print("开始部署券码系统到腾讯云云开发...")
    deploy_to_tcb()
    print("部署完成!")