import sys, requests, time, subprocess, argparse

API_URL = 'http://localhost:8000'

def start():
    print('🚀 Booting OmniSearch...')
    subprocess.run(['docker-compose', 'up', '-d', '--build'], check=True)
    print('⏳ Waiting 15s for DBs...')
    time.sleep(15)
    print('✅ System Ready. Use: python omni.py crawl <url>')

def stop():
    print('🛑 Shutting down...')
    subprocess.run(['docker-compose', 'down'], check=True)

def crawl(url, module):
    try:
        res = requests.post(f'{API_URL}/crawl', json={'url': url, 'module': module})
        print(f'🕷️  {res.json()["message"]}')
    except: print('❌ System offline. Run start first.')

def search(q, module):
    try:
        res = requests.get(f'{API_URL}/search', params={'q': q, 'module': module})
        print(f'\n🔍 Results in [{module}]:')
        for r in res.json().get('results', []):
            print(f'Found: {r["title"]} -> {r["url"]}')
    except: print('❌ System offline.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='cmd')
    subparsers.add_parser('start'); subparsers.add_parser('stop')
    c = subparsers.add_parser('crawl')
    c.add_argument('url'); c.add_argument('--type', default='general')
    s = subparsers.add_parser('search')
    s.add_argument('q'); s.add_argument('--type', default='general')
    args = parser.parse_args()
    if args.cmd == 'start': start()
    elif args.cmd == 'stop': stop()
    elif args.cmd == 'crawl': crawl(args.url, args.type)
    elif args.cmd == 'search': search(args.q, args.type)
