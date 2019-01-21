import Router from 'vue-router';
import Vue from 'vue';
import * as GirderComponents from '@girder/components/src/components';
import StudyList from './components/StudyList.vue';

Vue.use(Router);

export default new Router({
  routes: [{
    path: '/',
    component: StudyList,
  }, {
    path: '/auth',
    component: GirderComponents.Authentication,
  }],
});
