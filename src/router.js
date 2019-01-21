import Router from 'vue-router';
import Vue from 'vue';
import { components } from '@girder/components';
import StudyList from './components/StudyList.vue';

Vue.use(Router);

export default new Router({
  routes: [{
    path: '/',
    component: StudyList,
  }, {
    path: '/auth',
    component: components.Authentication,
  }],
});
