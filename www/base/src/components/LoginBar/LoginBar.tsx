/*
  This file is part of Buildbot.  Buildbot is free software: you can
  redistribute it and/or modify it under the terms of the GNU General Public
  License as published by the Free Software Foundation, version 2.

  This program is distributed in the hope that it will be useful, but WITHOUT
  ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
  FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
  details.

  You should have received a copy of the GNU General Public License along with
  this program; if not, write to the Free Software Foundation, Inc., 51
  Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

  Copyright Buildbot Team Members
*/

import './LoginBar.scss';
import {useContext} from 'react';
import {FaSignInAlt, FaSignOutAlt, FaUser} from 'react-icons/fa';
import {useLocation} from 'react-router-dom';
import {Nav, NavDropdown} from 'react-bootstrap';
import {ConfigContext} from 'buildbot-ui';
import {getBaseUrl} from 'buildbot-data-js';
import {LoginIcon} from '../LoginIcon/LoginIcon';

export const LoginBar = () => {
  const config = useContext(ConfigContext);
  const location = useLocation();

  const user = config.user;

  if (config.auth.name === 'NoAuth') {
    return <></>;
  }

  const redirect = location.pathname + location.search + location.hash;

  if (user.anonymous) {
    return (
      <Nav className="bb-loginbar-dropdown-nav">
        <NavDropdown title="Anonymous" id="bb-loginbar-dropdown">
          <NavDropdown.Item
            href={getBaseUrl(window.location, 'auth/login?redirect=' + encodeURI(redirect))}
          >
            {config.auth.oauth2 ? (
              <span>
                <LoginIcon iconName={config.auth.fa_icon} />
                &nbsp;Login with {config.auth.name}
              </span>
            ) : (
              <span>
                <FaSignInAlt />
                &nbsp;Login
              </span>
            )}
          </NavDropdown.Item>
        </NavDropdown>
      </Nav>
    );
  }

  const avatarURL = `avatar?username=${encodeURI(user.username ?? '')}&amp;email=${encodeURI(user.email ?? '')}`;

  const dropdownToggle =
    config.avatar_methods.length > 0 ? (
      <img alt={user.username ?? user.email ?? ''} src={avatarURL} className="avatar" />
    ) : (
      <span>
        {user.full_name ?? user.username ?? ''}
        <b className="caret"></b>
      </span>
    );

  const userDropdownHeader =
    user.full_name || user.email ? (
      <>
        <NavDropdown.Header>
          <FaUser />
          <span>
            {config.user.full_name ?? ''} {config.user.email ?? ''}
          </span>
        </NavDropdown.Header>
        <NavDropdown.Divider />
      </>
    ) : (
      <></>
    );

  return (
    <Nav className="bb-loginbar-dropdown-nav">
      <NavDropdown title={dropdownToggle} id="bb-loginbar-dropdown">
        {userDropdownHeader}
        <NavDropdown.Item
          href={getBaseUrl(window.location, 'auth/logout?redirect=' + encodeURI(redirect))}
        >
          <FaSignOutAlt />
          Logout
        </NavDropdown.Item>
      </NavDropdown>
    </Nav>
  );
};
