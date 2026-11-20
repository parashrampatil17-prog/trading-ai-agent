import tempfile, os
from broker_reconciliation import register_exit_watch, evaluate_exit_watch, reconcile_broker_state

class FakeKite:
    def __init__(self, orders=None, positions=None): self._o=orders or []; self._p=positions or {'net':[]}
    def orders(self): return self._o
    def positions(self): return self._p

def main():
    with tempfile.TemporaryDirectory() as d:
        db=os.path.join(d,'test.sqlite3')
        a=register_exit_watch(watch_id='w1',exchange='NFO',tradingsymbol='NIFTYTESTCE',quantity=50,average_price=100,stop_price=80,target_price=130,db_path=db)
        assert a['created']
        assert evaluate_exit_watch('w1',105,db_path=db)['status']=='WATCHING'
        x=evaluate_exit_watch('w1',79,db_path=db)
        assert x['trigger']=='STOP_LOSS' and x['broker_order_placed'] is False
        r=reconcile_broker_state(FakeKite(),db_path=db)
        assert r['ok']
    print('BROKER_RECONCILIATION_TESTS=PASS')
if __name__=='__main__': main()
