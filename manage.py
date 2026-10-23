#!/usr/bin/env python3
"""Local administrative commands; no public registration endpoint."""
import argparse, getpass, sqlite3, sys
from pathlib import Path
import server, auth

parser=argparse.ArgumentParser(description='管理 Web3 Scout 团队账号和数据库备份')
sub=parser.add_subparsers(dest='command',required=True)
p=sub.add_parser('user-add',help='新增账号或重置密码，并使旧会话失效');p.add_argument('username');p.add_argument('--password-stdin', action='store_true', help='从标准输入读取密码，仅用于受控自动化')
p=sub.add_parser('user-delete',help='删除团队账号');p.add_argument('username')
sub.add_parser('user-list',help='仅列出用户名')
p=sub.add_parser('backup',help='创建 SQLite 一致性备份，不覆盖现有文件');p.add_argument('destination')
args=parser.parse_args();server.init_db()
if args.command=='user-add':
    password=sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('新密码（至少 14 字符）: ')
    if not args.password_stdin and password!=getpass.getpass('再次输入: '):parser.error('两次密码不一致')
    try:
        with server.connect() as db:auth.set_user(db,args.username,password)
    except ValueError as e:parser.error(str(e))
    print('账号已保存，旧会话已失效。')
elif args.command=='user-list':
    with server.connect() as db:
        for row in db.execute('SELECT username FROM users ORDER BY username'):print(row[0])
elif args.command=='user-delete':
    with server.connect() as db:
        if db.execute('SELECT count(*) FROM users').fetchone()[0]<=1:parser.error('不能删除最后一个账号')
        db.execute('DELETE FROM sessions WHERE username=?',(args.username,))
        db.execute('DELETE FROM users WHERE username=?',(args.username,))
    print('账号已移除。')
elif args.command=='backup':
    target=Path(args.destination)
    if target.exists():parser.error('目标文件已存在，请使用新文件名')
    target.parent.mkdir(parents=True,exist_ok=True)
    with server.connect() as source,sqlite3.connect(target) as destination:source.backup(destination)
    target.chmod(0o600);print('数据库一致性备份完成。')
