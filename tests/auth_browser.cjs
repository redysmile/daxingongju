const { chromium }=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs'), os=require('node:os'), path=require('node:path'), crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'scout-auth-'));
const password=crypto.randomBytes(24).toString('base64url');
const env={...process.env,SCOUT_DB:path.join(temp,'auth.db'),SCOUT_REQUIRE_AUTH:'1',SCOUT_COOKIE_SECURE:'0',PORT:'3111'};
execFileSync('python3',['manage.py','user-add','tester','--password-stdin'],{cwd:root,env,input:password+'\n'});
const child=spawn('python3',['server.py'],{cwd:root,env,stdio:'ignore'});let browser;
(async()=>{
 for(let i=0;i<50;i++){try{if((await fetch('http://127.0.0.1:3111/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 browser=await chromium.launch({headless:true,executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:3111/');await page.getByRole('heading',{name:'欢迎回到研究工作台'}).waitFor();
 await page.locator('[name=username]').fill('tester');await page.locator('[name=password]').fill('wrong-password');await page.getByRole('button',{name:'登录研究工作台 →'}).click();await page.getByText('用户名或密码错误',{exact:true}).waitFor();
 await page.locator('[name=password]').fill(password);await page.getByRole('button',{name:'登录研究工作台 →'}).click();await page.getByRole('heading',{name:'每一个 Alpha，都始于一个信号。'}).waitFor();
 await page.getByRole('button',{name:'＋ 录入新项目',exact:true}).click();await page.locator('[name=name]').fill('Authenticated Browser Alpha');await page.locator('[name=owner]').fill('Assigned Researcher');await page.getByRole('button',{name:'保存基础信息'}).click();await page.locator('.modal').waitFor({state:'detached'});
 const state=await(await page.request.get('http://127.0.0.1:3111/api/state')).json();if(state.activity[0].actor!=='tester')throw Error('Audit actor is not the authenticated user');
 await page.getByRole('button',{name:'退出',exact:true}).click();await page.getByRole('heading',{name:'欢迎回到研究工作台'}).waitFor();
 if((await page.request.get('http://127.0.0.1:3111/api/state')).status()!==401)throw Error('Logout failed to revoke cookie');
 await page.setViewportSize({width:390,height:844});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile login overflow');
 if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS: login redirect, rejected password, valid login, authenticated create, verified audit actor, logout, private API, mobile login, no browser errors');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();child.kill();fs.rmSync(temp,{recursive:true,force:true})});
