'use strict';
const assert=require('node:assert/strict');
const filtering=require('../web/static/scoreboard-filters.js');
const rows=[
 {name:'甲',campus:'南山',teacher:'张老师',total:100,rank:1,kaohao:'secret1'},
 {name:'乙',campus:'广州',teacher:'李老师',total:90,rank:2,kaohao:'secret2'},
 {name:'丙',campus:'南山',teacher:'张老师',total:80,rank:3},
 {name:'丁',campus:'南山',teacher:'李老师',total:80,rank:3},
 {name:'未评测',campus:'南山',teacher:'张老师',total:0,rank:null},
 {name:'旧名单',campus:'',teacher:'',total:0,rank:null},
];
const state={rows};
const selected=filtering.rows(state,{campus:'=南山'},false);
assert.deepEqual(selected.map(r=>r.rank),[1,3,3,null]);
assert.deepEqual(selected.map(r=>r.filtered_rank),[1,2,2,null]);
assert.deepEqual(filtering.rows(state,{campus:'=南山',teacher:'=张老师'},true).map(r=>r.filtered_rank),[1,2,null]);
assert.equal(filtering.rows(state,{campus:'=广州',teacher:'=张老师'},false).length,0);
assert.equal(filtering.rows(state,{campus:'~'},false)[0].name,'旧名单');
assert.equal(filtering.rows(state,{search:'secret1'},false).length,0);
assert.equal(filtering.rows(state,{search:'secret1'},true).length,1);
assert.equal(filtering.rows(state,{search:'李老师'},false).length,2);
assert.deepEqual(filtering.rows(state,{search:'丙'},false).map(r=>r.filtered_rank),[1]);
assert.deepEqual(filtering.options(state,'teacher','=广州'),['李老师']);
assert.equal(filtering.matches('~','=~'),true);
assert.equal(filtering.matches('','=~'),false);
assert.deepEqual(state.rows,rows);assert.equal(rows[2].rank,3);assert.equal(rows[2].filtered_rank,undefined);
console.log('Leaderboard filters, ties, unranked rows, public/private search and nonmutating rank checks passed.');
